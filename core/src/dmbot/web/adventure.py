"""The Shared story adventure on the website (#3694; the Discord side is the Shared story card,
`dm_screen/adventure_card.py`).

Slice A: what DMbot holds of the DM's adventure, read-only. Each file given, how far its read is,
and what the read found, for a DM of the campaign only, from one door (`dmbot_web_adventure_list`,
migration 0634). Counts and the DM's own file names; never a word of the adventure, which is only
ever shown on the DM's own pages and never put in a log. The same numbers as Discord's card
(`story_plan`), so the page can only say what the card says.
"""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import PurePath
from typing import Any

from dmbot import credits
from dmbot.db import Database
from dmbot.memory.adventure import CAMPAIGN_MAX_CHARS, DOCUMENTS_MAX
from dmbot.memory.name_documents import max_bytes_for
from dmbot.pictures.bucket import BucketError
from dmbot.web.lore_pdf import Bucket
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def _estimate(document: dict[str, Any]) -> str | None:
    """What reading this file will cost, in the card's own sentence ("About 120 AI credits for the
    adventure."), from the same table the read is metered by (`credits.CREDITS_PER_UNIT`). None for
    a file with no text (restored from a backup) and for one already read. Counts only."""
    read = document.get("read")
    if document["restored"] or (read and read["status"] == "done"):
        return None
    return credits.adventure_estimate_words(int(document["characters"]))


async def held(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{mode, documents: [{id, name, characters, restored, addedAt, read, found, estimate}],
    documentsMax,
    charactersMax, charactersHeld}`. `read` is null until a read began, else `{status, nextChunk,
    totalChunks, stopReason, skippedParts, updatedAt}` (`status`: reading, done or stopped).
    `found` is `{chapters, places, people, links}`. `restored`: back from a backup, with no text
    until the file is given again. A DM of the campaign only: anyone else is "not found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_list(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    documents = [dict(d, estimate=_estimate(d)) for d in got["documents"]]
    return {
        "mode": got["mode"],
        "documents": documents,
        "documentsMax": DOCUMENTS_MAX,
        "charactersMax": CAMPAIGN_MAX_CHARS,
        "charactersHeld": sum(int(d["characters"]) for d in documents),
    }


PAGE = 50  # the door's own cap too


async def read_nodes(db: Database, session: Session, ref: str, offset: int = 0) -> dict[str, Any]:
    """Slice B: a page of what was read, as Discord's "See what was read" lists it: `{total,
    offset, items: [{kind, name, summary, quote}]}`, `kind` being chapter, place, person or link.
    This is the book's own content: a DM of the campaign only (anyone else is "not found"), never
    logged, never cached here, and one page per call."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    start = max(0, offset)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_plan_nodes(%s, %s, %s, %s) AS r", (guild_id, campaign_id, start, PAGE)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"total": got["total"], "offset": start, "items": got["items"]}


_ID = re.compile(r"^[0-9a-f]{32}$")


async def remove(db: Database, session: Session, ref: str, document: str) -> str:
    """Slice C: queue the removal of one file, as Discord's 🗑 does it (the bot calls the same
    `AdventureStore.remove`). Returns the removal's id for `removal`. A DM of the campaign only;
    "not found" for anyone else, a malformed id and a file that is not there, so the page can't
    tell the cases apart."""
    if not _ID.match(document):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_remove(%s, %s, %s) AS r", (guild_id, campaign_id, document)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return str(got["id"])


async def removal(db: Database, session: Session, ref: str, removal_id: str) -> dict[str, Any]:
    """How a queued removal went: `{state: waiting|working|done|failed, error, updatedAt}`, where
    `error` is a code (`not_yours`, `failed`) the page turns into words."""
    if not _ID.match(removal_id):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_remove_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, removal_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("missing"):
        raise RecapError("not_found")
    return {"id": got["id"], "state": got["state"], "error": got["error"],
            "updatedAt": got["updatedAt"]}  # fmt: skip


log = logging.getLogger(__name__)

# The file types Discord's 📖 takes (`dm_screen/adventure_card.FILE_TYPES`), and the biggest the
# website takes: a PDF up to the lore PDF's own website cap, anything else up to the core's 10 MB.
FILE_TYPES = (".pdf", ".docx", ".txt", ".md", ".text")
PDF_MAX_BYTES = 50 * 1024 * 1024


def max_bytes(name: str) -> int:
    return PDF_MAX_BYTES if PurePath(name).suffix.casefold() == ".pdf" else max_bytes_for(name)


def sniff(name: str, data: bytes) -> bool:
    """Whether the bytes are the kind of file the name says, by their own first bytes (what the
    sender or the browser called it does not count): `%PDF-` for a PDF, a zip's `PK` for a Word
    file, and for text no zero byte at all."""
    suffix = PurePath(name).suffix.casefold()
    if suffix == ".pdf":
        return data.startswith(b"%PDF-")
    if suffix == ".docx":
        return data.startswith(b"PK\x03\x04")
    return b"\x00" not in data[:8192]


def clean_name(name: str) -> str:
    """The file's own name, no folders, no control characters, at most 120 characters (the
    adventure store cleans it again the way Discord's does)."""
    base = PurePath(name.replace("\\", "/")).name
    return "".join(c for c in base if c.isprintable())[:120].strip()


_GIVE_REFUSALS = {"not_on", "too_many", "busy", "too_soon"}


async def give(
    db: Database, session: Session, ref: str, bucket: Bucket | None, name: str, data: bytes
) -> str:
    """Slice D: stage a file the DM sent and queue it for the bot (which reads it with Discord's
    own reader and keeps it with the same call). Returns the id the page polls with `giving`.
    Refusals are `RecapError` codes: `uploads_off` (no bucket), `not_found` (not a DM of the
    campaign), `bad_file` (empty, too big, the wrong type or not what its name says), `not_on`,
    `too_many`, `busy` and `too_soon` (the door's own), `failed` (the bucket did not take it)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if bucket is None:
        raise RecapError("uploads_off")
    shown = clean_name(name)
    suffix = PurePath(shown).suffix.casefold()
    if (
        not shown
        or suffix not in FILE_TYPES
        or not data
        or len(data) > max_bytes(shown)
        or not sniff(shown, data)
    ):
        raise RecapError("bad_file")
    key = f"a/{secrets.token_hex(16)}{suffix}"
    try:
        await bucket.put(key, data)
    except BucketError as exc:
        log.warning("A sent adventure file could not be staged (%s)", exc)
        raise RecapError("failed") from None
    try:
        async with db.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild_id)
            cur = await conn.execute(
                "SELECT dmbot_web_adventure_give(%s, %s, %s, %s, %s) AS r",
                (guild_id, campaign_id, key, shown, len(data)),
            )
            found = await cur.fetchone()
    except BaseException:
        await _forget(bucket, key)
        raise
    got = found["r"] if found else None
    if got is None or got["status"] != "ok":
        await _forget(bucket, key)
        status = None if got is None else str(got["status"])
        raise RecapError(status if status in _GIVE_REFUSALS else "not_found")
    return str(got["id"])


async def _forget(bucket: Bucket, key: str) -> None:
    """Best effort: an object left behind goes with the bucket's 1-day rule."""
    try:
        await bucket.delete(key)
    except BucketError as exc:
        log.warning("A staged adventure file could not be deleted (%s)", exc)


async def giving(db: Database, session: Session, ref: str, give_id: str) -> dict[str, Any]:
    """How a given file is going, in the shared progress shape: `{state, stage: waiting|reading|
    saving|done|failed, error, name, sizeBytes, characters, chunks, createdAt, updatedAt,
    serverNow}`. `error` is a code (`unreadable`, `expired`, `already`, `too_many`, `full`,
    `no_text`, `not_on`, `not_yours`, `refused`, `failed`) the page turns into words."""
    if not _ID.match(give_id):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_give_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, give_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("missing"):
        raise RecapError("not_found")
    return dict(got)


_READ_REFUSALS = {"gone", "not_readable", "already_read", "busy", "too_soon"}


async def read(db: Database, session: Session, ref: str, document: str) -> str:
    """Slice E: queue "Yes, read it" for one file. The page shows the file's estimate (`held`)
    first and asks the DM; this is the yes. Returns the id `reading` polls. The bot then makes
    the plan check, the caps and `PlanStore.begin` again, as Discord's button does. Refusals are
    `RecapError` codes: `not_found` (not a DM, or a malformed id), `gone`, `not_readable` (give
    the file again), `already_read`, `busy`, `too_soon`."""
    if not _ID.match(document):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_read(%s, %s, %s) AS r", (guild_id, campaign_id, document)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]) if got["status"] in _READ_REFUSALS else "not_found")
    return str(got["id"])


async def reading(db: Database, session: Session, ref: str, read_id: str) -> dict[str, Any]:
    """Whether the read has started: `{state: waiting|working|done|failed, error, documentId,
    createdAt, updatedAt, serverNow}`. `done` means started: from then the read's own progress
    (stage, how far, when it last moved) is the file's `read` in `held`. `error` is a code
    (`plan`, `busy`, `not_on`, `not_yours`, `gone`, `not_readable`, `already_read`,
    `document_full`, `month_full`, `failed`) the page turns into words."""
    if not _ID.match(read_id):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_read_state(%s, %s, %s) AS r",
            (guild_id, campaign_id, read_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("missing"):
        raise RecapError("not_found")
    return dict(got)


_GUIDE_REFUSALS = {"not_guide", "gone", "bad_job", "busy", "too_soon"}


async def guide(
    db: Database, session: Session, ref: str, document: str, job: str, chapter: str = ""
) -> str:
    """Slice F: queue a Guide me job for one file (`read`; `pick` with a chapter id: "I'm in
    another chapter"; `clear`: back to DMbot's guess). Returns the id `guiding` polls. The answer
    is made by the bot with the same function as Discord's 🧭. Refusals are `RecapError` codes:
    `not_found` (not a DM, or a malformed id), `gone`, `not_guide` (the Shared story switch is not
    on "Guide me"), `bad_job`, `busy`, `too_soon`."""
    if not _ID.match(document) or (chapter and not _ID.match(chapter)):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_guide(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, document, job, chapter),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]) if got["status"] in _GUIDE_REFUSALS else "not_found")
    return str(got["id"])


async def guiding(db: Database, session: Session, ref: str, guide_id: str) -> dict[str, Any]:
    """A Guide me job's state, and its answer once done: `{state: waiting|working|done|failed,
    error, job, documentId, answer, createdAt, updatedAt, serverNow}`. `answer` is `{text,
    picked, chapter: {id, name} | null, chapters: [{id, name}]}` (the book's own names: the DM's
    page only, never logged). `error` is a code (`not_yours`, `not_guide`, `plan`, `no_chapters`,
    `gone`, `failed`) the page turns into words."""
    if not _ID.match(guide_id):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_guide_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, guide_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("missing"):
        raise RecapError("not_found")
    return dict(got)


_MATCH_REFUSALS = {"not_on", "bad_job", "nothing_to_undo", "busy", "too_soon"}


async def match(
    db: Database,
    session: Session,
    ref: str,
    job: str,
    picks: list[tuple[str, str, str]] | None = None,
    source: str = "",
) -> str:
    """Slice G1: queue a Match to files job (`suggest`; `decide` with up to ten
    `(book entry id, table entry id, "same" | "not_same")`; `undo` of an earlier decide job's id).
    Returns the id `matching` polls. The bot does it with the calls Discord's 🔗 makes. Refusals are
    `RecapError` codes: `not_found` (not a DM, or a malformed id), `not_on` (the Shared story
    switch is off, or no DM confirmed the right to use the material), `bad_job`,
    `nothing_to_undo`, `busy`, `too_soon`."""
    if source and not _ID.match(source):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    from psycopg.types.json import Jsonb  # (a database type: kept out of the module's top)

    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_match(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, job, Jsonb([list(p) for p in picks or []]), source),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]) if got["status"] in _MATCH_REFUSALS else "not_found")
    return str(got["id"])


async def matching(db: Database, session: Session, ref: str, match_id: str) -> dict[str, Any]:
    """A Match to files job's state, and its answer once done: `{state: waiting|working|done|
    failed, error, job, canUndo, answer, createdAt, updatedAt, serverNow}`. `answer` is:
    - `suggest`: `{pairs: [{planId, planName, planKind, entityId, entityName}]}`;
    - `decide`, `pick` and `unpick`: `{written, refusal, at}`, where `refusal` is a code for the
      first pair it would not take (`gone`, `clash`, `twice`, `taken_book`, `taken_table` or
      `failed`, "" when none) and `at` is that pick's index in the list sent, null when none;
    - `options`: `{entity: {id, name}, linked: [{planId, name, kind, chapter}], options: [{planId,
      name, kind, chapter}], total, offset, pageSize}`;
    - `undo`: `{undone, note}` (`note`: "", `too_late` or `changed_since`).
    The names are the book's and the table's own: the DM's page only, never logged. `canUndo` is
    true on a done `decide`, `pick` or `unpick` that wrote something: pass its id as `source` to
    `match` with `undo`. `error` is a code (`not_yours`, `not_on`, `gone`, `failed`) the page turns
    into words (`gone`: the table entry is not there any more)."""
    if not _ID.match(match_id):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_match_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, match_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("missing"):
        raise RecapError("not_found")
    return dict(got)


ENTRY_JOBS = ("options", "pick", "unpick")


async def match_entry(
    db: Database,
    session: Session,
    ref: str,
    job: str,
    entity: str,
    plan: str = "",
    skip: int = 0,
) -> str:
    """Slice G2: queue a job for one table entry (Discord's "Same as in the book…" on a name's
    card): `options` (the book entries it could be, 25 a page from `skip`, and the ones it is),
    `pick` (it is this book entry, `plan`) or `unpick` (it is no longer). Returns the id `matching`
    polls, as for `match`; a pick or an unpick writes one change, which the `undo` job takes back
    (`canUndo`). Refusals are `RecapError` codes as for `match`."""
    if not _ID.match(entity) or (plan and not _ID.match(plan)):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_match_entry(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, job, entity, plan, skip),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]) if got["status"] in _MATCH_REFUSALS else "not_found")
    return str(got["id"])
