"""The bot's side of the website's Recap style (#3060): what a DM chose there, applied here.

The website has no right on the style tables, so a DM's choice about how their recaps sound (add an
example recap, remove one, use DMbot's own again, add a standing note, remove one) is queued as a
row in `web_recap_style_actions` (migration 0290). This module claims the rows one at a time in the
order made and applies each with the same store calls and checks as Discord's ✍️ Recap style panel
(`StyleStore`, `NoteStore`): the DM check, three examples, 600 words, ten notes of 300 characters.

A recap pasted as an example, or a typed note, is the DM's own writing and may name secrets: the
queue blanks `arg` in the very statement that ends the row, whichever way it ends, and sweeps what
nobody took. Only the action name and the outcome word are ever logged, never the words or who
asked.

A typed note goes through `sort_feedback`, the same step Discord's note editor uses, so it is kept
as one tidy style note like every other, never as a correction or a request to invent. Discord's
panel has no "add a note": the website adds it on purpose.

Checked again at the moment of use, because the queue-time check can be stale: the sender must
still be a DM of the campaign.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Database
from dmbot.recaps import feedback as fb
from dmbot.recaps import style
from dmbot.rules.house import HouseRuleError

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # a claim quiet this long belongs to a bot that stopped (as in 0290's doors)
NOT_TAKEN_AFTER_S = 1800  # nobody took it in half an hour: it ends as `not_taken`
KEEP_FOR_S = 86400  # nothing stays longer than a day

ADD_EXAMPLE, REMOVE_EXAMPLE, USE_DEFAULTS = "add_example", "remove_example", "use_defaults"
ADD_NOTE, REMOVE_NOTE = "add_note", "remove_note"

# Why a choice failed: one word each. The page knows `full`, `gone`, `too_long`, `empty` and
# `not_a_note`, and says "Nothing was changed" for the rest.
FULL = "full"  # three examples already
GONE = "gone"  # the example or the note is not there
TOO_LONG = "too_long"
EMPTY = "empty"
NOT_A_NOTE = "not_a_note"  # the sort did not keep exactly one style note
NOT_A_DM = "not_a_dm"
FAILED = "failed"  # anything else, a bug included: the row must never stay "working" for it


@dataclass(frozen=True, slots=True)
class Claim:
    """A choice the bot holds. `claimed_at` (milliseconds) is the claim: only the worker that holds
    the current one may write. `arg` is the DM's own words (or an id)."""

    id: str
    campaign_id: str
    user_id: int
    action: str
    arg: str
    claimed_at: int


class StyleQueue:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def sweep(self, guild_id: int) -> None:
        """What the website's doors also do, so a DM's words never wait on a DM who did not come
        back and a queued paste is never applied hours late: a claim quiet for ten minutes goes
        back to waiting, a choice nobody took in half an hour fails as `not_taken` with its words
        blanked, a finished row's words are blanked, and rows older than a day go."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM web_recap_style_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, now - KEEP_FOR_S),
            )
            await conn.execute(
                "UPDATE web_recap_style_actions SET arg = ''"
                " WHERE guild_id = %s AND state IN ('done', 'failed') AND arg <> ''",
                (guild_id,),
            )
            await conn.execute(
                "UPDATE web_recap_style_actions SET state = 'waiting', claimed_at = 0,"
                " updated_at = %s WHERE guild_id = %s AND state = 'working' AND updated_at < %s",
                (now, guild_id, now - RETAKE_AFTER_S),
            )
            await conn.execute(
                "UPDATE web_recap_style_actions SET state = 'failed', error = 'not_taken',"
                " arg = '', updated_at = %s"
                " WHERE guild_id = %s AND state = 'waiting' AND updated_at < %s",
                (now, guild_id, now - NOT_TAKEN_AFTER_S),
            )

    async def claim_next(self, guild_id: int) -> Claim | None:
        """The oldest waiting choice of this server, marked as being worked on."""
        now = self._now()
        claimed = int(now * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_recap_style_actions SET state = 'working', claimed_at = %s,"
                " updated_at = %s WHERE (guild_id, id) = (SELECT guild_id, id"
                " FROM web_recap_style_actions WHERE guild_id = %s AND state = 'waiting'"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, action, arg",
                (claimed, int(now), guild_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Claim(
            row["id"], row["campaign_id"], int(row["user_id"]), row["action"], row["arg"], claimed
        )

    async def hold(self, guild_id: int, claim: Claim) -> bool:
        """Still the holder of the claim? Touches the time, which also keeps a slow step (the sort
        of a note) from being taken for a dead worker. Asked just before anything is changed, so a
        row that was put back and taken by another worker is never applied twice."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_recap_style_actions SET updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), guild_id, claim.id, claim.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, claim: Claim, error: str = "") -> bool:
        """Done, or failed with a word. The words are blanked in this same statement, whichever
        way it ends. Only the worker that holds the claim can write. False: not its row any
        more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_recap_style_actions SET state = %s, error = %s, arg = '',"
                " updated_at = %s WHERE guild_id = %s AND id = %s AND state = 'working'"
                " AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error,
                    int(self._now()),
                    guild_id,
                    claim.id,
                    claim.claimed_at,
                ),
            )
            return bool(cur.rowcount)


_WORDS: dict[str, str] = {
    style.FULL: FULL,
    style.NOT_FOUND: GONE,
    style.NOTE_GONE: GONE,
    style.TOO_LONG: TOO_LONG,
    style.EMPTY: EMPTY,
}


def error_of(exc: Exception, arg: str = "") -> str:
    """The word for a store's refusal. A note that is "empty or too long" is told apart by its
    length, because the page has a word for each."""
    if isinstance(exc, HouseRuleError):
        return NOT_A_DM  # the store's DM check (`require_dm`)
    words = str(exc)
    if words == style.NOTE_BAD:
        return TOO_LONG if len(" ".join(arg.split())) > style.NOTE_CHARS else EMPTY
    return _WORDS.get(words, FAILED)


def _id(arg: str) -> int | None:
    """An example or note id, or None when it is not one (the door only lets digits through)."""
    if not (arg.isascii() and arg.isdigit()) or len(arg) > 18:
        return None
    return int(arg)


async def apply(bot: Any, queue: StyleQueue, guild_id: int, claim: Claim) -> str:
    """Apply one claimed choice. The error word, or "" when it is done. Never raises for a
    refusal; a bug is caught by the caller and ends the row as `failed`."""
    campaign = await bot.campaigns.get(guild_id, claim.campaign_id)
    if campaign is None or claim.user_id not in campaign.dm_user_ids:
        return NOT_A_DM
    styles, notes = bot.recap_styles, bot.recap_notes
    if styles is None or notes is None:
        return FAILED
    action, arg = claim.action, claim.arg
    cid, uid = campaign.id, claim.user_id
    try:
        if action == ADD_NOTE:
            return await _add_note(bot, queue, guild_id, claim, campaign)
        if action in (REMOVE_EXAMPLE, REMOVE_NOTE):
            found = _id(arg)
            if found is None:
                return GONE
            if not await queue.hold(guild_id, claim):
                return ""  # (not its row any more: the other worker ends it)
            if action == REMOVE_EXAMPLE:
                await styles.remove(guild_id, cid, uid, found)
            else:
                await notes.remove(guild_id, cid, uid, found)
        elif action == ADD_EXAMPLE:
            if not await queue.hold(guild_id, claim):
                return ""
            await styles.add(guild_id, cid, uid, arg)
        elif action == USE_DEFAULTS:
            if not await queue.hold(guild_id, claim):
                return ""
            await styles.use_defaults(guild_id, cid, uid)
        else:
            return FAILED
    except (CampaignError, HouseRuleError) as exc:
        return error_of(exc, arg)
    return ""


async def _add_note(bot: Any, queue: StyleQueue, guild_id: int, claim: Claim, campaign: Any) -> str:
    """A typed note, sorted as Discord's note editor sorts one: kept only as exactly one style
    note, never as a correction or a request to invent. What is stored is the DM's own words (not
    what the AI wrote back)."""
    words = " ".join(claim.arg.split())
    if not words:
        return EMPTY
    if len(words) > style.NOTE_CHARS:
        return TOO_LONG
    ai = bot.recap_feedback_ai
    if ai is None or await bot.recap_feedback_gate(campaign) is not None:
        return FAILED  # no AI, recaps off for the plan, or too many sorts lately: nothing is stored
    sorted_ = await fb.sort_feedback(ai, words)
    if sorted_ is None:
        return FAILED
    if (
        len(sorted_.style) != 1
        or sorted_.corrections
        or sorted_.replies
        or fb.asks_to_invent(words)
    ):
        return NOT_A_NOTE
    if not await queue.hold(guild_id, claim):
        return ""
    try:
        await bot.recap_notes.add(guild_id, campaign.id, claim.user_id, words)
    except (CampaignError, HouseRuleError) as exc:
        return error_of(exc, words)
    return ""
