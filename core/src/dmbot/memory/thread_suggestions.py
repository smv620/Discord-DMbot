"""The lore reader's story-thread suggestions, waiting for the DM (#4453, quests slice 1; design
`docs/design/quests.md` §9.1 and §9.6; Supervisor's ruling on #4453): one row a bundle, kept or
turned down as a whole from ❓ Questions for you.

A suggestion is a wait, never story memory (migration 0502): nothing in it is an entry until the DM
keeps it, so no names list, count of names, "Add all" or players' view reads it. Its one reader is
`ThreadSuggestionStore` here (CI: `test_thread_suggestion_readers`), and only the DM's card and
the upload call it. ✅ Keep goes through `MemoryStore.keep_thread`: the thread, its state and the
chosen people in one change, with this row marked kept in the same transaction (`mark_kept`), so
one Undo takes it all back and ↩ on the card puts the suggestion back (`unkeep`). 🚫 marks it
turned down; its name key stops it coming back, and so do its people (`known`). Since slice 3
(#4500, migration 0507) a suggestion also holds the parts and branches read with it (`extra`); the
keep writes those the DM left on. Since slice 5a (#4522, migration 0509) a suggestion may come from
a session: it holds the session, the numbers of the lines it quotes and who said them, never their
words, and waits only while all its speakers are recorded (`purge_stopped`, `forget`: the rule of
every after-session proposal, Supervisor's ruling B on #4522). Counts only in the log, never
names."""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import Changes
from dmbot.memory.models import name_key, new_id
from dmbot.memory.thread_read import ReadBranch, ReadPart, Suggestion

log = logging.getLogger(__name__)

WAITING, KEPT, TURNED_DOWN = "waiting", "kept", "turned_down"
GONE = "That suggestion was already answered. Here are the ones still waiting."
UPLOAD_MAX = 200


@dataclass(frozen=True, slots=True)
class Waiting:
    """One suggestion as the DM's card shows it: entry ids only (the card names them)."""

    id: str
    kind: str
    name: str
    aim: str
    quote: str
    parts: tuple[tuple[str, str], ...]
    upload: str
    created_at: int
    reads: tuple[ReadPart, ...] = ()  # (slice 3, #4500: its parts, then its branches)
    branches: tuple[ReadBranch, ...] = ()
    session_id: str | None = None  # (slice 5a: read from this session's lines, not from lore)
    line_starts: tuple[int, ...] = ()
    speaker_ids: tuple[int, ...] = ()

    @property
    def lines(self) -> tuple[ReadPart | ReadBranch, ...]:
        """What the DM can switch off before keeping, in the card's order: the parts, then the
        branches. `keep_thread`'s `leave_off` counts in this order."""
        return (*self.reads, *self.branches)

    @property
    def state(self) -> str:
        from dmbot.memory.thread_read import STATE

        return STATE[self.kind]


def _parts(raw: Any) -> tuple[tuple[str, str], ...]:
    rows = raw if isinstance(raw, list) else json.loads(raw or "[]")
    return tuple(
        (str(p[0]), str(p[1])) for p in rows if isinstance(p, (list, tuple)) and len(p) == 2
    )


def extra_json(s: Suggestion) -> str:
    """The suggestion's parts and branches as stored (0507): entry ids only, never free names."""
    return json.dumps(
        {
            "reads": [
                {
                    "kind": p.kind, "words": p.words, "state": p.state, "quote": p.quote,
                    "who": p.who_id, "due": p.due, "terms": p.terms, "limit_kind": p.limit_kind,
                }
                for p in s.reads
            ],
            "branches": [
                {
                    "kind": b.kind, "name": b.name, "aim": b.aim, "quote": b.quote,
                    "state": b.state, "parts": [list(x) for x in b.parts],
                }
                for b in s.branches
            ],
        }
    )  # fmt: skip


def _text(row: dict[str, Any], key: str) -> str:
    v = row.get(key)
    return v if isinstance(v, str) else ""


def from_extra(raw: Any) -> tuple[tuple[ReadPart, ...], tuple[ReadBranch, ...]]:
    """The stored parts and branches; a row from before 0507 (or anything unreadable) has none,
    and keeps as it did. Never raises: one bad row must not hide the whole 📜 list."""
    try:
        data = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return (), ()
    if not isinstance(data, dict):
        return (), ()
    raw_reads, raw_branches = data.get("reads"), data.get("branches")
    if not isinstance(raw_reads, list):
        raw_reads = []
    if not isinstance(raw_branches, list):
        raw_branches = []
    reads = tuple(
        ReadPart(
            _text(r, "kind"), _text(r, "words"), _text(r, "state"), _text(r, "quote"),
            r["who"] if isinstance(r.get("who"), str) else None, _text(r, "due"),
            _text(r, "terms"), _text(r, "limit_kind"),
        )
        for r in raw_reads if isinstance(r, dict)
    )  # fmt: skip
    branches = tuple(
        ReadBranch(
            _text(b, "kind"), _text(b, "name"), _text(b, "aim"), _text(b, "quote"),
            _text(b, "state"), _parts(b.get("parts", [])),
        )
        for b in raw_branches if isinstance(b, dict)
    )  # fmt: skip
    return reads, branches


async def lines_in(
    w: Changes, suggestion_id: str
) -> tuple[tuple[ReadPart, ...], tuple[ReadBranch, ...]]:
    """Inside the keep's own write (`MemoryStore.keep_thread`): the waiting suggestion's parts and
    branches, so what is kept is what was suggested, never what a screen sent."""
    cur = await w.conn.execute(
        "SELECT extra FROM thread_suggestions"
        " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'waiting'",
        (w.guild_id, w.campaign_id, suggestion_id),
    )
    row = await cur.fetchone()
    return from_extra(row["extra"]) if row is not None else ((), ())


async def session_at_in(w: Changes, suggestion_id: str) -> int | None:
    """Inside the keep's own write: the start of the session a suggestion was read from, so the
    kept thread starts at it (None for one from lore, or one no longer waiting)."""
    cur = await w.conn.execute(
        "SELECT session_at FROM thread_suggestions"
        " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'waiting'",
        (w.guild_id, w.campaign_id, suggestion_id),
    )
    row = await cur.fetchone()
    return None if row is None or row["session_at"] is None else int(row["session_at"])


async def mark_kept(w: Any, suggestion_id: str, batch: int | None) -> bool:
    """Inside the keep's own write (`MemoryStore.keep_thread`): the suggestion is kept, with the
    change's number. False when it was answered meanwhile (a second press, another screen)."""
    cur = await w.conn.execute(
        "UPDATE thread_suggestions SET status = 'kept', kept_batch = %s"
        " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'waiting'",
        (batch, w.guild_id, w.campaign_id, suggestion_id),
    )
    return bool(cur.rowcount)


def unkeep_in(batch: int) -> Any:
    """For `MemoryStore.undo`'s `after`: the suggestion a keep made waits again, inside the Undo's
    own write, so the thread and the suggestion never part (a stop between two writes would lose
    the suggestion unseen: the reviewer's finding on #4453)."""

    async def after(w: Any) -> None:
        await w.conn.execute(
            "UPDATE thread_suggestions SET status = 'waiting', kept_batch = NULL"
            " WHERE guild_id = %s AND campaign_id = %s AND kept_batch = %s AND status = 'kept'"
            + _CAN_WAIT,
            (w.guild_id, w.campaign_id, batch),
        )

    return after


# A session suggestion waits again (an Undo, ↩) only while no withdrawal has touched it: `forget`
# empties its lines, and its name and aim may hold the words of whoever stopped, so it stays
# answered (the reviewer on #4522), never waiting again.
async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """A session's transcript is deleted: the suggestions read from it that still wait go (their
    evidence is gone); a kept or turned-down one is the DM's answer and stays, without its lines,
    and never waits again. In the caller's transaction; returns how many went."""
    cur = await conn.execute(
        "DELETE FROM thread_suggestions WHERE guild_id = %s AND session_id = %s"
        " AND status = 'waiting'",
        (guild_id, session_id),
    )
    await conn.execute(
        "UPDATE thread_suggestions SET line_starts = '{}', speaker_ids = '{}'"
        " WHERE guild_id = %s AND session_id = %s AND status <> 'waiting'",
        (guild_id, session_id),
    )
    return int(cur.rowcount or 0)


async def _still_there(
    conn: Any, guild_id: int, campaign_id: str, session_id: str, found: Sequence[Suggestion]
) -> list[Suggestion]:
    """The session suggestions whose speakers all still have lines in the session and are still
    recorded, read under the session row's lock (#4091, #4153; `MetStore.add`'s way): "Delete my
    transcripts", a DM's removal of a speaker and Stop recording me take this lock or re-check
    after it, so a suggestion quoting someone gone is never written."""
    from dmbot.consent import still_recorded

    cur = await conn.execute(
        "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
        " AND id = %s FOR UPDATE",
        (guild_id, campaign_id, session_id),
    )
    if await cur.fetchone() is None:
        return []  # (the session is gone, or not this campaign's)
    cur = await conn.execute(
        "SELECT DISTINCT user_id FROM transcript_lines WHERE guild_id = %s AND session_id = %s",
        (guild_id, session_id),
    )
    present = {int(r["user_id"]) for r in await cur.fetchall()}
    present = set(await still_recorded(conn, guild_id, campaign_id, present))
    return [s for s in found if set(s.speaker_ids) <= present]


_CAN_WAIT = " AND (session_id IS NULL OR cardinality(line_starts) >= 1)"


def _ids(people: Collection[int] | None) -> list[int] | None:
    return None if people is None else sorted({int(p) for p in people})


async def forget(conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None) -> int:
    """Stop recording me, "Delete my transcripts" and a DM's removal of one speaker (only that
    campaign's, `campaign_id`): delete the waiting suggestions that quote this person. A kept or
    turned-down one is the DM's answer and stays, without this person's id and its lines, and
    never waits again (`_CAN_WAIT`). Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM thread_suggestions WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status = 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    gone = int(cur.rowcount)
    # (answered rows only: an Undo that made a row wait meanwhile is deleted above next time,
    # never left waiting with nobody, which the CHECK refuses)
    await conn.execute(
        "UPDATE thread_suggestions SET speaker_ids = array_remove(speaker_ids, %s),"
        " line_starts = '{}' WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status <> 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (user_id, guild_id, user_id, campaign_id, campaign_id),
    )
    return gone


@dataclass(frozen=True, slots=True)
class KeptFrom:
    """Where a kept thread came from, for "Where was it said?" (#4554): pointers only, never
    words. A session's lines are found by when each began and who said it; a lore one has its
    upload's label."""

    session_id: str | None
    line_starts: tuple[int, ...]
    speaker_ids: tuple[int, ...]
    upload: str
    created_at: int


class ThreadSuggestionStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def kept_from(self, guild_id: int, campaign_id: str, entity_id: str) -> KeptFrom | None:
        """The suggestion the DM kept that made this entry, found through the change that added
        it (the keep writes both under one number). None for an entry no suggestion made, or one
        whose suggestion was undone. A DM-only reader: the upload's label can name a backstory."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT t.session_id, t.line_starts, t.speaker_ids, t.upload, t.created_at"
                " FROM thread_suggestions t JOIN memory_changes c"
                " ON c.guild_id = t.guild_id AND c.campaign_id = t.campaign_id"
                " AND c.batch = t.kept_batch"
                " WHERE t.guild_id = %s AND t.campaign_id = %s AND t.status = 'kept'"
                " AND c.table_name = 'memory_entities' AND c.op = 'insert' AND c.row_id = %s"
                " ORDER BY t.created_at DESC LIMIT 1",
                (guild_id, campaign_id, entity_id),
            )
            r = await cur.fetchone()
        if r is None:
            return None
        return KeptFrom(
            None if r["session_id"] is None else str(r["session_id"]),
            tuple(int(n) for n in r["line_starts"] or ()),
            tuple(int(n) for n in r["speaker_ids"] or ()),
            str(r["upload"]),
            int(r["created_at"]),
        )

    async def known(self, guild_id: int, campaign_id: str) -> list[tuple[str, frozenset[str]]]:
        """Every suggestion's name key and people, whatever became of it: the reader never
        suggests one again (`thread_read.check`'s `have`)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT name_key, parts, extra FROM thread_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        known = [(str(r["name_key"]), frozenset(e for _, e in _parts(r["parts"]))) for r in rows]
        # (a branch waiting inside another suggestion counts too, so a later upload never suggests
        # "Who killed Wit?" again as a thread of its own: the reviewer on #4500)
        for r in rows:
            for b in from_extra(r["extra"])[1]:
                known.append((name_key(b.name), frozenset()))
        return known

    async def offer(
        self,
        guild_id: int,
        campaign_id: str,
        found: Sequence[Suggestion],
        upload: str,
        now: int,
        *,
        session_id: str | None = None,
        session_at: int | None = None,
    ) -> int:
        """Keep these suggestions waiting; one already made under the same name is left as it
        is (turned down stays turned down). How many are new. From a session (`session_id`), each
        carries its lines and speakers (`session_threads`), and one with no speaker is never kept
        (the consent sweep could not reach it)."""
        if session_id is not None:
            found = [s for s in found if s.speaker_ids]
        made = 0
        async with self._db.guild(guild_id) as conn:
            if session_id is not None and found:
                found = await _still_there(conn, guild_id, campaign_id, session_id, found)
            for s in found:
                cur = await conn.execute(
                    "INSERT INTO thread_suggestions (guild_id, campaign_id, id, kind, name,"
                    " name_key, aim, quote, parts, upload, status, created_at, extra, session_id,"
                    " session_at, line_starts, speaker_ids)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'waiting', %s, %s, %s, %s,"
                    " %s, %s) ON CONFLICT (guild_id, campaign_id, name_key) DO NOTHING",
                    (
                        guild_id, campaign_id, new_id(), s.kind, s.name[:120],
                        name_key(s.name)[:120] or s.kind, s.aim[:240], s.quote[:400],
                        json.dumps([list(p) for p in s.parts]), upload[:UPLOAD_MAX], max(now, 1),
                        extra_json(s), session_id, session_at if session_id is not None else None,
                        list(s.line_starts) if session_id is not None else [],
                        list(s.speaker_ids) if session_id is not None else [],
                    ),
                )  # fmt: skip
                made += cur.rowcount
        if found:
            log.info("Story thread suggestions: %d new of %d", made, len(found))
        return made

    async def waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int] | None = None
    ) -> list[Waiting]:
        """The suggestions waiting for the DM, oldest first. With `opted_in` (who is recorded in
        the server now), the consent sweep runs first, so no session suggestion of someone who
        stopped is ever shown."""
        if opted_in is not None:
            await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, kind, name, aim, quote, parts, upload, created_at, extra, session_id,"
                " line_starts, speaker_ids FROM thread_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'waiting' ORDER BY created_at, id",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            Waiting(
                str(r["id"]), str(r["kind"]), str(r["name"]), str(r["aim"]), str(r["quote"]),
                _parts(r["parts"]), str(r["upload"]), int(r["created_at"]),
                *from_extra(r["extra"]),
                session_id=None if r["session_id"] is None else str(r["session_id"]),
                line_starts=tuple(int(n) for n in r["line_starts"] or ()),
                speaker_ids=tuple(int(n) for n in r["speaker_ids"] or ()),
            )
            for r in rows
        ]  # fmt: skip

    async def count(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int] | None = None
    ) -> int:
        """How many wait, changing nothing; with `opted_in`, a session suggestion with a speaker
        who is no longer recorded is not counted (the sweep deletes it when the card is opened)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM thread_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'waiting'"
                " AND (%s::bigint[] IS NULL OR session_id IS NULL OR speaker_ids <@ %s::bigint[])",
                (guild_id, campaign_id, *([_ids(opted_in)] * 2)),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row is not None else 0

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Delete the waiting session suggestions with a speaker who is not recorded in this
        server now: the consent re-check on the read path, as for reveals and deeds. A lore one
        has no speakers; a kept or turned-down one is the DM's answer and stays."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM thread_suggestions WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'waiting' AND session_id IS NOT NULL"
                " AND NOT (speaker_ids <@ %s::bigint[])",
                (guild_id, campaign_id, _ids(opted_in)),
            )
            gone = int(cur.rowcount)
        if gone:
            log.info("Story thread suggestions: %d dropped (a speaker stopped recording)", gone)
        return gone

    async def turn_down(self, guild_id: int, campaign_id: str, suggestion_id: str) -> bool:
        """🚫: never suggested again. False when it was answered meanwhile."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_suggestions SET status = 'turned_down'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'waiting'",
                (guild_id, campaign_id, suggestion_id),
            )
            return bool(cur.rowcount)

    async def unkeep(self, guild_id: int, campaign_id: str, batch: int) -> bool:
        """↩ Undo of a keep (the caller undid the change first): the suggestion waits again. A
        turned-down one comes back only through ↩ on its own card (`unturn`)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_suggestions SET status = 'waiting', kept_batch = NULL"
                " WHERE guild_id = %s AND campaign_id = %s AND kept_batch = %s"
                " AND status = 'kept'" + _CAN_WAIT,
                (guild_id, campaign_id, batch),
            )
            return bool(cur.rowcount)

    async def unturn(self, guild_id: int, campaign_id: str, suggestion_id: str) -> bool:
        """↩ Undo of a 🚫 on the card: the suggestion waits again."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_suggestions SET status = 'waiting'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND status = 'turned_down'" + _CAN_WAIT,
                (guild_id, campaign_id, suggestion_id),
            )
            return bool(cur.rowcount)
