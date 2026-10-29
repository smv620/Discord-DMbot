"""The bot's side of the website's character sheets (#2817; the page is #2816): what a player or a
DM chose there, applied here.

The website never fetches a link and has no right on the sheet tables, so a choice (link my D&D
Beyond sheet, forget it, read the campaign's sheets again) is queued as a row (`web_sheet_actions`).
This module claims the rows one at a time in the order made and applies each with the same store
calls as Discord's 📜 buttons, so the hints for a running session, the keep rules and the words in
the DM's cards all follow. The bot is the only thing that talks to D&D Beyond.

Checked again at the moment of use, because the queue-time check can be stale: the asker must still
be in the server, still play the character (a DM may have given it to someone else) and, for a
read-again, still be a DM of the campaign. The cooldowns (20 s a person for a link, 60 s a
campaign for a read-again) are shared with Discord's buttons, since each link reads D&D Beyond.

A typed sheet (class, level, species, names) is saved as Discord's "Type in my character" form saves
it. A document link (#2849) is read as Discord's "Use a document link" reads it: the same fetch
(`dmbot.fetch`: https only, public addresses re-checked after every redirect, size and time caps),
the same one AI read, the same one-at-a-time rule and daily count (`memory.sheet_document`). The
link and the text live only in the call: when the row finishes its `url` and `payload` are blanked,
and only outcome words are logged, never a link or a word of a document.

Idempotent, because the website's door puts a choice that has been "working" for ten minutes back
to "waiting": a link twice is the same link (the snapshot stays for the same link by the same
player), a read-again twice is two reads, and a forget that finds nothing left to forget on a
*retried* row is done (the first try did it). Only outcome words are logged, never a link beyond
what the row says, never a sheet's contents.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dmbot import fetch, plan_rules
from dmbot.ai import AIError
from dmbot.campaigns import Campaign
from dmbot.db import Database
from dmbot.discord_limits import shorten
from dmbot.memory import sheet_document, sheets
from dmbot.memory.name_documents import DocumentError
from dmbot.memory.sheet_store import PlayerCharacter, SheetRefused

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped: the door's own limit
KEEP_FOR_S = 86400  # as long as the website reads an answer back

LINK, FORGET, READ_AGAIN, TYPED, DOCUMENT = "link", "forget", "read_again", "typed", "document"

# What a failed choice says, for the page to turn into plain words (`_` there is `-`).
NOT_ALLOWED = "not_allowed"  # not theirs to change, or they left the server
NOT_FOUND = "not_found"  # the campaign, the character or the sheet is gone
BAD_LINK = "bad_link"  # not a D&D Beyond character link
TOO_SOON = "too_soon"  # the link cooldown, checked again
READ_TOO_SOON = "read_too_soon"  # the read-again cooldown: the page words it apart
# Two notes on a link that was saved: the choice is `done` and the page says what to do, as Discord
# does ("Linked, but ..."). Not failures.
NOT_PUBLIC = "not_public"  # D&D Beyond refused it: set the sheet to Public
UNREACHABLE = "unreachable"  # D&D Beyond did not answer; the link is kept and read later
NOTES = (NOT_PUBLIC, UNREACHABLE)
BAD_TYPED = "bad_typed"  # the typed form's JSON is not the shape the door writes
BAD_DOCUMENT = "bad_document"  # the link could not be opened (not public, not https, not shared)
CANT_READ = "cant_read"  # the document opened but holds no sheet DMbot can read
NO_AI = "no_ai"  # reading documents is not set up, or the campaign's plan has no AI
USED_UP = "used_up"  # the server's reads for today
BUSY = "busy"  # another read for this server is running; send it again
TOO_BIG = "too_big"
SERVER = "server"  # anything unexpected; the choice can be sent again


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    campaign_id: str
    user_id: int
    entity_id: str
    action: str
    url: str
    payload: str = ""  # a typed sheet's form as JSON; "" for the others
    claimed_at: int = 0  # this worker's claim: only it may finish the row
    retried: bool = False  # the door put it back after a slow try: the first try may have done it


class Host(Protocol):
    """What applying a choice needs from the running bot, so tests can stand in for Discord."""

    sheets: Any  # the SheetStore (link, save, unlink, characters_of)
    sheet_ai: Any  # the AI client that reads documents; None when it isn't set up

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def in_server(self, guild_id: int, user_id: int) -> bool:
        """False only when Discord says they are not in the server; any other failure raises."""
        ...

    async def fetch_sheet(self, character: int) -> dict[str, Any]:
        """One read of D&D Beyond. Raises `sheets.SheetError`."""
        ...

    async def read_all(
        self, guild_id: int, campaign_id: str, progress: Callable[[], Awaitable[None]]
    ) -> None:
        """Read every linked sheet of the campaign again, as Discord's Refresh sheets does, and
        call `progress` after each sheet so the page sees the choice still moving."""
        ...

    def link_too_soon(self, guild_id: int, user_id: int) -> bool:
        """True inside the person's link cooldown; otherwise counts this one."""
        ...

    def read_too_soon(self, guild_id: int, campaign_id: str) -> bool: ...

    def document_too_soon(self, guild_id: int, user_id: int, campaign_id: str) -> bool:
        """True inside the person's document cooldown (60 s a campaign); otherwise counts this
        one."""
        ...

    async def plan_gate(
        self, action: plan_rules.Action, guild_id: int, campaign: Campaign, user_id: int
    ) -> str | None:
        """Why this person may not use the AI for the campaign (its plan), or None."""
        ...

    def sheets_changed(self, guild_id: int, campaign_id: str) -> None:
        """A session running for the campaign takes the new names from its next clip on."""
        ...


class ActionStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Action | None:
        """The oldest waiting choice (or a stalled one) of this server, marked as being worked on
        (`working`/`reading`, which the page shows), never past an earlier open one of the same
        campaign: the order made is the order done, so Link then Forget never run backwards."""
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id,"
                " (updated_at > created_at OR state = 'working') AS retried"
                " FROM web_sheet_actions WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " AND NOT EXISTS (SELECT 1 FROM web_sheet_actions p WHERE p.guild_id = %s"
                "  AND p.campaign_id = web_sheet_actions.campaign_id"
                "  AND p.seq < web_sheet_actions.seq"
                "  AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_sheet_actions a SET state = 'working', stage = 'reading',"
                " claimed_at = %s, updated_at = %s FROM pick"
                " WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.entity_id, a.action, a.url,"
                " a.payload, pick.retried",
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
        return Action(
            row["id"],
            row["campaign_id"],
            int(row["user_id"]),
            row["entity_id"],
            row["action"],
            row["url"],
            row["payload"],
            now_ms,
            bool(row["retried"]),
        )

    async def touch(self, guild_id: int, item: Action) -> bool:
        """The choice is still moving: the page's "no news" line and the door's ten-minute
        put-back both read `updated_at`. Only the claiming worker."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_sheet_actions SET updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, item: Action, error: str) -> bool:
        """Done, or failed with why, for the page to read; a note (`NOTES`) is done with the
        word. Only the worker that holds the current claim can write. False: not its row any
        more. The link and the typed form are blanked in the same statement: a finished row keeps
        no copy of what the player gave (#2849)."""
        state = "failed" if error and error not in NOTES else "done"
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_sheet_actions SET state = %s, stage = %s, error = %s, updated_at = %s,"
                " url = '', payload = ''"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    state,
                    state,
                    error[:40],
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete choices older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_sheet_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(host: Host, guild_id: int, item: Action, store: ActionStore | None = None) -> str:
    """Apply one queued choice the way Discord's button would. The error word, "" when done.
    Never raises for a refusal."""
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if campaign is None:
        return NOT_FOUND
    if not await host.in_server(guild_id, item.user_id):
        return NOT_ALLOWED
    if item.action == READ_AGAIN:
        return await _read_again(host, guild_id, campaign, item, store)
    if item.action not in (LINK, FORGET, TYPED, DOCUMENT):
        return SERVER
    mine = await host.sheets.characters_of(guild_id, item.user_id, campaign.id)
    if not any(_is(c, item.entity_id) for c in mine):  # still theirs, still a live character
        return NOT_ALLOWED
    if item.action == FORGET:
        return await _forget(host, guild_id, campaign, item)
    if item.action == TYPED:
        return await _typed(host, guild_id, campaign, item)
    if item.action == DOCUMENT:
        return await _document(host, guild_id, campaign, item, store)
    return await _link(host, guild_id, campaign, item)


def _is(character: PlayerCharacter, entity_id: str) -> bool:
    return character.entity_id == entity_id


async def _read_again(
    host: Host, guild_id: int, campaign: Campaign, item: Action, store: ActionStore | None
) -> str:
    if item.user_id not in campaign.dm_user_ids:  # checked again: they may have handed over
        return NOT_ALLOWED
    if not any(s.url is not None for s in await host.sheets.sheets(guild_id, campaign.id)):
        return ""  # nothing linked: nothing to read, and no cooldown spent (as in Discord)
    if host.read_too_soon(guild_id, campaign.id):
        return READ_TOO_SOON

    async def progress() -> None:
        if store is not None:
            await store.touch(guild_id, item)

    await host.read_all(guild_id, campaign.id, progress)
    return ""


async def _forget(host: Host, guild_id: int, campaign: Campaign, item: Action) -> str:
    gone = await host.sheets.unlink(guild_id, campaign.id, item.entity_id, player=item.user_id)
    if gone:
        host.sheets_changed(guild_id, campaign.id)
        return ""
    # Nothing left to forget: on a row the door put back, the first try already did it.
    return "" if item.retried else NOT_FOUND


async def _link(host: Host, guild_id: int, campaign: Campaign, item: Action) -> str:
    character = sheets.character_id(item.url)
    if character is None or sheets.sheet_url(character) != item.url:
        return BAD_LINK  # (the door accepts one exact shape, and so does this)
    if host.link_too_soon(guild_id, item.user_id):
        return TOO_SOON
    store = host.sheets
    try:
        await store.link(guild_id, campaign.id, item.entity_id, character, player=item.user_id)
    except SheetRefused:
        return NOT_ALLOWED
    try:
        try:
            snapshot = await host.fetch_sheet(character)
        except sheets.SheetError as exc:
            log.info("A sheet linked on the website couldn't be read yet")
            return NOT_PUBLIC if exc.refused else UNREACHABLE  # the link stays; it is read later
        kept = await store.save(
            guild_id, campaign.id, item.entity_id, snapshot, int(time.time()), url=item.url
        )
    finally:
        host.sheets_changed(guild_id, campaign.id)  # a new link drops the old names too
    return "" if kept else NOT_FOUND  # forgotten or linked again while it was read


def _form(payload: str) -> tuple[str, int, str, list[str]] | None:
    """(class, level, species, names) from the door's JSON, or None when it isn't that shape.
    The same split as Discord's form: commas and semicolons."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    class_name, level = data.get("className"), data.get("level")
    species, names = data.get("species", ""), data.get("names", "")
    if not (isinstance(class_name, str) and 1 <= len(class_name.strip()) <= 60):
        return None
    if not isinstance(level, int) or isinstance(level, bool):
        return None
    if not 1 <= level <= sheets.LEVEL_MAX:
        return None
    if not (isinstance(species, str) and len(species) <= 60):
        return None
    if not (isinstance(names, str) and len(names) <= 1500):
        return None
    listed = [n.strip() for n in names.replace(";", ",").split(",") if n.strip()]
    return class_name, level, species, listed


async def _character_name(host: Host, guild_id: int, item: Action) -> str | None:
    for c in await host.sheets.characters_of(guild_id, item.user_id, item.campaign_id):
        if _is(c, item.entity_id):
            return str(c.name)
    return None


async def _typed(host: Host, guild_id: int, campaign: Campaign, item: Action) -> str:
    """Discord's "Type in my character", from the page: replaces a linked sheet, as there."""
    form = _form(item.payload)
    name = await _character_name(host, guild_id, item)
    if form is None or name is None:
        return BAD_TYPED if name is not None else NOT_FOUND
    if host.link_too_soon(
        guild_id, item.user_id
    ):  # (shares the link cooldown; a bad form spends none)
        return TOO_SOON
    class_name, level, species, listed = form
    snapshot = sheets.typed(
        shorten(name, sheets.NAME_MAX),
        species=species,
        class_name=class_name,
        level=level,
        names=listed,
    )
    if snapshot is None:  # never save a bad one
        return BAD_TYPED
    try:
        await host.sheets.save(
            guild_id,
            campaign.id,
            item.entity_id,
            snapshot,
            int(time.time()),
            player=item.user_id,
        )
    except SheetRefused:
        return NOT_ALLOWED
    finally:
        host.sheets_changed(guild_id, campaign.id)
    return ""


async def _document(
    host: Host, guild_id: int, campaign: Campaign, item: Action, store: ActionStore | None
) -> str:
    """Discord's "Use a document link", from the page, without an interaction. The row is touched
    at each step (link opened, AI answered, saved) so the page shows it still moving."""

    async def moved() -> None:
        if store is not None:
            await store.touch(guild_id, item)

    ai = host.sheet_ai
    if ai is None or await host.plan_gate("ai", guild_id, campaign, item.user_id) is not None:
        return NO_AI
    name = await _character_name(host, guild_id, item)
    if name is None:
        return NOT_FOUND
    day = sheet_document.today()
    # No await from `refusal` to `claim`: two reads for a server can't both pass.
    stopped = sheet_document.refusal(guild_id, day)
    if stopped is not None:
        return BUSY if stopped == sheet_document.BUSY else USED_UP
    if host.document_too_soon(guild_id, item.user_id, campaign.id):
        return TOO_SOON
    sheet_document.claim(guild_id, day)
    spent = False  # the AI is asked: only then does the read count against the day
    try:
        try:
            text = await sheet_document.link_text(item.url)
        except (sheet_document.TooBig, fetch.TooBig):
            return TOO_BIG
        except fetch.Unreachable:
            return SERVER  # the site didn't answer: the page offers a retry
        except fetch.LinkError:
            return BAD_DOCUMENT
        except DocumentError:
            return CANT_READ
        await moved()
        spent = True
        try:
            snapshot, _ = await sheet_document.read_snapshot(ai, text)
        except AIError:
            return NO_AI
        except TimeoutError:
            return SERVER  # nothing saved; the page offers a retry
        await moved()
        if snapshot is None:
            return CANT_READ
        try:
            await host.sheets.save(
                guild_id,
                campaign.id,
                item.entity_id,
                snapshot,
                int(time.time()),
                player=item.user_id,
            )
        except SheetRefused:
            return NOT_ALLOWED
        finally:
            host.sheets_changed(guild_id, campaign.id)
        await moved()
        return ""
    finally:
        if not spent:  # a refusal, a bug or a stop before the AI: no read used
            sheet_document.give_back(guild_id, day)
        sheet_document.release(guild_id)


async def run_one(host: Host, store: ActionStore, guild_id: int, item: Action) -> bool:
    """Apply one claimed choice and mark it. A bug never leaves the row "working": it ends as
    `failed` with `server` for the page to offer a retry. False when the row could not be marked
    (the loop stops; the row is taken again later and `apply` is safe to run twice). Only the
    outcome word is logged."""
    try:
        error = await apply(host, guild_id, item, store)
    except Exception as exc:
        log.warning("A website sheet choice failed: %s", type(exc).__name__)
        error = SERVER
    try:
        if not await store.finish(guild_id, item, error):
            log.info("A website sheet choice was taken over by another worker")
    except Exception as exc:
        log.warning("A website sheet choice could not be marked: %s", type(exc).__name__)
        return False
    log.info("Website sheet %s: %s", item.action, error or "done")  # (a note is done)
    return True
