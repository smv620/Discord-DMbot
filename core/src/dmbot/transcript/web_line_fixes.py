"""The bot's side of a line fixed from the website (#2883; the core half is #2858): a queued fix
(`web_line_fixes`) is claimed in order, checked again, and applied with the calls Discord's
`/dmbot fix` makes (`ui.line_fix.submit` and `submit_dm`).

The line's own speaker *suggests*: the question goes to the DM screen with Accept and No, and the
DM decides. A DM of the campaign *rewrites* a spoken line at once through `Bot.apply_line_fix`.
The door checked what it could in the database; everything that can have changed since is checked
again here: the words, that the line is still there and still reads as it did when the person saw it
(`basis`), who is still a DM, and that the speaker still agrees to be recorded. A suggestion is
never checked against the secret names (Discord checks only when the DM accepts, so a player can
never learn whether their words named one); a rewrite is.

The person's words sit in the row only while it is open. Every end, done or failed, blanks them in
the same UPDATE as the state, and the sweep deletes whatever is a day old, so a row the bot never
finished cannot keep them. Logs carry outcome words only, never the words or the line.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.campaigns import Campaign
from dmbot.db import Database
from dmbot.transcript import suggestions
from dmbot.transcript.suggestions import Own, Suggestion, SuggestionStore

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped: the door's own limit
KEEP_FOR_S = 86400  # as long as the website reads an answer back

SUGGEST, REWRITE = "suggest", "rewrite"
SENT, CHANGED = "sent", "changed"  # what a done fix tells the person

# What a failed fix says, for the page to turn into plain words (the list is the door's `REASONS`).
PLAYER_STOPPED = "player_stopped"  # the speaker no longer agrees to be recorded
SECRET = "secret"  # a rewrite that would put a secret name in the transcript
LINE_CHANGED = "line_changed"  # the line reads differently from when the person saw it
LINE_GONE = "line_gone"
NOT_WRITTEN = "not_written"
NOT_AVAILABLE = "not_available"
OPEN = "open"  # the line already has a suggestion waiting
MANY = "many"  # three suggestions already waiting
NO_DM_SCREEN = "no_dm_screen"
FAILED = "failed"  # anything else, including a bug


@dataclass(frozen=True, slots=True)
class Fix:
    id: str
    campaign_id: str
    user_id: int  # who asked
    session_id: str
    speaker: int
    started_ms: int
    mode: str
    words: str
    basis: str
    claimed_at: int = 0  # this worker's claim: only it may finish the row


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    result: str = ""  # SENT or CHANGED when done
    undo: bool = False  # a rewrite of a line in a session that still runs


class Host(Protocol):
    """What applying a fix needs from the running bot, so tests can stand in for Discord."""

    suggestions: SuggestionStore | None

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    def fix_has_consent(self, guild_id: int, user_id: int) -> bool: ...

    async def fix_names_a_secret(self, guild_id: int, campaign_id: str, text: str) -> bool:
        """True when the words are or sound like a secret name, and also when the names could not
        be read (as in Discord: never write what could not be checked)."""
        ...

    async def apply_line_fix(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        user_id: int,
        started_ms: int,
        text: str,
        *,
        noted: bool = False,
    ) -> bool: ...

    def fix_session_running(self, guild_id: int, session_id: str) -> bool: ...

    async def fix_post_suggestion(
        self,
        guild_id: int,
        channel_id: int,
        made: Suggestion,
        line: Own,
        text: str,
    ) -> bool:
        """Put the question on the DM screen with its buttons. False: it could not be posted."""
        ...


class ActionStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Fix | None:
        """The oldest waiting fix (or a stalled one) of this server, marked as being worked on,
        never past an earlier open one of the same campaign: the order made is the order done."""
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_line_fixes"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " AND NOT EXISTS (SELECT 1 FROM web_line_fixes p WHERE p.guild_id = %s"
                "  AND p.campaign_id = web_line_fixes.campaign_id"
                "  AND p.seq < web_line_fixes.seq"
                "  AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_line_fixes a SET state = 'working', stage = 'fixing',"
                " claimed_at = %s, updated_at = %s FROM pick"
                " WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.session_id, a.speaker,"
                " a.started_ms, a.mode, a.words, a.basis",
                (
                    guild_id,
                    now_ms - RETAKE_AFTER_S * 1000,
                    guild_id,
                    now_ms - RETAKE_AFTER_S * 1000,
                    now_ms,
                    now,
                ),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Fix(
            row["id"],
            row["campaign_id"],
            int(row["user_id"]),
            row["session_id"],
            int(row["speaker"]),
            int(row["started_ms"]),
            row["mode"],
            row["words"],
            row["basis"],
            now_ms,
        )

    async def finish(self, guild_id: int, item: Fix, outcome: Outcome) -> bool:
        """Done or failed with why, and the words blanked in the same statement (the table
        refuses a finished row that keeps them). Only the worker that holds the current claim can
        write. False: not its row any more."""
        state = "failed" if outcome.error else "done"
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_line_fixes SET state = %s, stage = %s, error = %s, result = %s,"
                " undo = %s, words = '', updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    state,
                    state,
                    outcome.error[:40],
                    "" if outcome.error else outcome.result,
                    outcome.undo and not outcome.error,
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def basis_now(self, guild_id: int, item: Fix) -> str | None:
        """The hash of the line's cleaned words as they read now, made the way the door made it
        (in SQL, not Python's `split`, so the two always agree). None: no such spoken line."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT md5(btrim(regexp_replace(coalesce(text, heard), '\\s+', ' ', 'g')))"
                " AS basis FROM transcript_lines"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL AND NOT typed ORDER BY id LIMIT 1",
                (item.session_id, item.speaker, item.started_ms),
            )
            row = await cur.fetchone()
        return None if row is None else str(row["basis"])

    async def sweep(self, guild_id: int) -> int:
        """Delete fixes older than `KEEP_FOR_S`, whatever their state: a row the bot never
        finished must not keep a person's words past a day."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_line_fixes WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(host: Host, store: ActionStore, guild_id: int, item: Fix) -> Outcome:
    """Apply one claimed fix the way Discord's would. Never raises for a refusal."""
    sug = host.suggestions
    if sug is None:
        return Outcome(NOT_AVAILABLE)
    text = suggestions.tidy(item.words)  # the door only checked the length
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if text is None or campaign is None:
        return Outcome(FAILED)
    line = await sug.line(guild_id, item.session_id, item.speaker, item.started_ms)
    if line is None:
        return Outcome(LINE_GONE)
    if await store.basis_now(guild_id, item) != item.basis:  # changed since they saw it
        return Outcome(LINE_CHANGED)
    if text == " ".join(line.text.split()):
        return Outcome(FAILED)  # nothing to change
    if item.mode == SUGGEST:
        return await _suggest(host, sug, guild_id, campaign, item, line, text)
    if item.mode == REWRITE:
        return await _rewrite(host, guild_id, campaign, item, text)
    return Outcome(FAILED)


async def _suggest(
    host: Host,
    sug: SuggestionStore,
    guild_id: int,
    campaign: Campaign,
    item: Fix,
    line: Own,
    text: str,
) -> Outcome:
    """`ui.line_fix.submit` without the interaction. No secret-name check, on purpose."""
    if item.user_id != item.speaker:  # only the line's own speaker suggests
        return Outcome(FAILED)
    if not host.fix_has_consent(guild_id, item.speaker):
        return Outcome(PLAYER_STOPPED)
    channel = campaign.dm_screen_channel_id
    if channel is None:
        return Outcome(NO_DM_SCREEN)
    made = await sug.suggest(
        guild_id, campaign.id, item.session_id, item.speaker, item.started_ms, text
    )
    if made == OPEN:
        return Outcome(OPEN)
    if made == MANY:
        return Outcome(MANY)
    if isinstance(made, str):
        return Outcome(FAILED)  # "none": the line is not theirs
    if not await host.fix_post_suggestion(guild_id, channel, made, line, text):
        # The DM can't see it: don't leave it waiting unseen.
        await sug.decide(guild_id, made.id, suggestions.DROPPED, item.speaker)
        return Outcome(NO_DM_SCREEN)
    return Outcome(result=SENT)


async def _rewrite(host: Host, guild_id: int, campaign: Campaign, item: Fix, text: str) -> Outcome:
    """`ui.line_fix.submit_dm` without the interaction."""
    if item.user_id not in campaign.dm_user_ids:  # checked again: they may have handed over
        return Outcome(FAILED)
    if not host.fix_has_consent(guild_id, item.speaker):
        return Outcome(PLAYER_STOPPED)
    if await host.fix_names_a_secret(guild_id, campaign.id, text):
        return Outcome(SECRET)
    try:
        written = await host.apply_line_fix(
            guild_id,
            campaign.id,
            item.session_id,
            item.speaker,
            item.started_ms,
            text,
            noted=True,
        )
    except Exception as exc:
        log.warning("A website line fix could not be written: %s", type(exc).__name__)
        written = False
    if not written:
        return Outcome(NOT_WRITTEN)
    # Discord's Undo exists only while the session runs (the same test `submit_dm` makes).
    return Outcome(result=CHANGED, undo=host.fix_session_running(guild_id, item.session_id))


async def run_one(host: Host, store: ActionStore, guild_id: int, item: Fix) -> bool:
    """Apply one claimed fix and mark it. A bug never leaves the row "working": it ends as
    `failed`. False when the row could not be marked (the loop stops; the row is taken again
    later). Only the outcome word is logged."""
    try:
        outcome = await apply(host, store, guild_id, item)
    except Exception as exc:
        log.warning("A website line fix failed: %s", type(exc).__name__)
        outcome = Outcome(FAILED)
    try:
        if not await store.finish(guild_id, item, outcome):
            log.info("A website line fix was taken over by another worker")
    except Exception as exc:
        log.warning("A website line fix could not be marked: %s", type(exc).__name__)
        return False
    log.info("Website line fix %s: %s", item.mode, outcome.error or "done")
    return True
