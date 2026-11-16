"""Progress on story threads the campaign already has, read from a session, waiting for the DM
(#4522, quests slice 5b; design `docs/design/quests.md` §9.5; Supervisor's ruling A and B on
#4522; migration 0520).

Each line names its thread and one change (`Change`): a new part (an objective, a complication, a
reward given, help, a time limit's words), a part it has moving on (an objective done, help
returned…), or the thread's own state (a hook taken up is "Happening now"). The DM sees them on the
📜 card under the thread's name, "Progress to check", and keeps the ones left on in **one change**
(`MemoryStore.keep_thread_progress`), so one ↩ Undo takes them all back (`unkeep_in`).

The rules every after-session proposal keeps (0509, the reveals' and deeds'):
- **No words of the transcript:** a line holds the session, when the lines it quotes began and who
  said them; its `words` are the reader's few words of what changed, as a part's are.
- **Consent:** a waiting line is shown and counted only while every speaker it quotes is recorded
  (`purge_stopped`, `count`'s filter), and Stop recording me deletes every waiting line that
  quotes the person (`forget`); a deleted session takes its waiting lines (`forget_session`). A
  kept or turned-down line is the DM's answer and stays, without that person and the lines, and
  never waits again (`_CAN_WAIT`).
- **Offered once:** the same change on the same thread (`change_key`) is never offered twice.
- **A time limit is words only:** its date is never set from what was said; the DM gives it, as
  slice 4c's offer.

Only ids and counts are logged, never a line's words.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import thread_parts
from dmbot.memory.models import ABANDONED, GOING, RESOLVED, new_id

log = logging.getLogger(__name__)

PART, PART_STATE, THREAD_STATE = "part", "part_state", "thread_state"
KINDS = (PART, PART_STATE, THREAD_STATE)
# The parts a session can add (§9.5): a reward *promised* is the thread's own, set when it is kept,
# so a session gives one only as given.
PART_KINDS = (
    thread_parts.OBJECTIVE, thread_parts.COMPLICATION, thread_parts.REWARD_GIVEN,
    thread_parts.HELP, thread_parts.TIME_LIMIT,
)  # fmt: skip
# The states a thread's own line may set: going on, finished or dropped. A hint or "not started"
# is never read from play: something happened.
THREAD_STATES = (GOING, RESOLVED, ABANDONED)
GONE = "Those were answered already."
WORDS_MAX, STATE_MAX = thread_parts.WORDS_MAX, 30


@dataclass(frozen=True, slots=True)
class Change:
    """What one line would change on its thread, as the reader gave it and code checked it."""

    kind: str
    part_kind: str = ""
    words: str = ""
    state: str = ""
    part_id: str | None = None
    due: str = ""  # a time limit's words, never a date

    def key(self, thread_id: str) -> str:
        """The change in a fixed form, hashed: offered once on each thread."""
        fixed = json.dumps(
            [thread_id, self.kind, self.part_kind, " ".join(self.words.lower().split()),
             self.state, self.part_id or ""],
            separators=(",", ":"),
        )  # fmt: skip
        return hashlib.sha256(fixed.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Line:
    """One waiting line of progress, as the card shows it (the card reads the quoted lines back
    from the transcript when it is shown)."""

    id: str
    thread_id: str
    change: Change
    session_id: str
    session_at: int | None
    line_starts: tuple[int, ...]
    speaker_ids: tuple[int, ...]
    created_at: int


def check(change: Change) -> Change | None:
    """The change if it is one a line may make, cut to the table's limits; None otherwise (pure).
    A part's state must be one of its kind's (the thread's own rules, `thread_parts.STATES`)."""
    words = " ".join(change.words.split())[:WORDS_MAX]
    due = " ".join(change.due.split())[:WORDS_MAX]
    if change.kind == PART:
        if change.part_kind not in PART_KINDS or not words:
            return None
        states = thread_parts.STATES[change.part_kind]
        state = change.state if change.state in states else states[0]
        return Change(PART, change.part_kind, words, state, None, due)
    if change.kind == PART_STATE:
        if not change.part_id or not change.state or len(change.state) > STATE_MAX:
            return None
        return Change(PART_STATE, "", words, change.state, change.part_id)
    if change.kind == THREAD_STATE:
        if change.state not in THREAD_STATES:
            return None
        return Change(THREAD_STATE, "", words, change.state)
    return None


def _line(r: Any) -> Line:
    return Line(
        str(r["id"]), str(r["thread_id"]),
        Change(
            str(r["kind"]), str(r["part_kind"]), str(r["words"]), str(r["state"]),
            None if r["part_id"] is None else str(r["part_id"]), str(r["due"]),
        ),
        str(r["session_id"]), None if r["session_at"] is None else int(r["session_at"]),
        tuple(int(n) for n in r["line_starts"] or ()),
        tuple(int(n) for n in r["speaker_ids"] or ()), int(r["created_at"]),
    )  # fmt: skip


_COLUMNS = (
    "id, thread_id, kind, part_kind, words, state, part_id, due, session_id, session_at,"
    " line_starts, speaker_ids, created_at"
)
# A line waits again (an Undo) only while no withdrawal has touched it: `forget` empties its lines
_CAN_WAIT = " AND cardinality(line_starts) >= 1"


def _ids(people: Collection[int] | None) -> list[int] | None:
    return None if people is None else sorted({int(p) for p in people})


async def waiting_in(w: Any, line_ids: Sequence[str]) -> list[Line]:
    """Inside the keep's own write: these lines, locked, if they still wait."""
    cur = await w.conn.execute(
        f"SELECT {_COLUMNS} FROM thread_progress WHERE guild_id = %s AND campaign_id = %s"
        " AND id = ANY(%s) AND status = 'waiting' ORDER BY created_at, id FOR UPDATE",
        (w.guild_id, w.campaign_id, sorted(set(line_ids))),
    )
    return [_line(r) for r in await cur.fetchall()]


async def mark_kept(w: Any, line_ids: Sequence[str], batch: int | None) -> int:
    """Inside the keep's own write: these lines are kept, with the change's number."""
    cur = await w.conn.execute(
        "UPDATE thread_progress SET status = 'kept', kept_batch = %s WHERE guild_id = %s"
        " AND campaign_id = %s AND id = ANY(%s) AND status = 'waiting'",
        (batch, w.guild_id, w.campaign_id, sorted(set(line_ids))),
    )
    return int(cur.rowcount or 0)


def unkeep_in(batch: int) -> Any:
    """For `MemoryStore.undo`'s `after`: the lines a keep made wait again, inside the Undo's own
    write, so the change and the lines never part."""

    async def after(w: Any) -> None:
        await w.conn.execute(
            "UPDATE thread_progress SET status = 'waiting', kept_batch = NULL"
            " WHERE guild_id = %s AND campaign_id = %s AND kept_batch = %s AND status = 'kept'"
            + _CAN_WAIT,
            (w.guild_id, w.campaign_id, batch),
        )

    return after


async def forget(conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None) -> int:
    """Stop recording me, "Delete my transcripts" and a DM's removal of one speaker (only that
    campaign's): delete the waiting lines that quote this person. A kept or turned-down line is
    the DM's answer and stays, without this person's id and its lines. Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM thread_progress WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status = 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE thread_progress SET speaker_ids = array_remove(speaker_ids, %s),"
        " line_starts = '{}' WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status <> 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (user_id, guild_id, user_id, campaign_id, campaign_id),
    )
    return gone


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """A session's transcript is deleted: its waiting lines go; an answered one stays, without its
    lines, and never waits again. In the caller's transaction."""
    cur = await conn.execute(
        "DELETE FROM thread_progress WHERE guild_id = %s AND session_id = %s"
        " AND status = 'waiting'",
        (guild_id, session_id),
    )
    await conn.execute(
        "UPDATE thread_progress SET line_starts = '{}', speaker_ids = '{}'"
        " WHERE guild_id = %s AND session_id = %s AND status <> 'waiting'",
        (guild_id, session_id),
    )
    return int(cur.rowcount or 0)


class ThreadProgressStore:
    """The waiting lines: offer, list, count, turn down, and the consent sweep. The keep is the
    memory store's (`MemoryStore.keep_thread_progress`), one change with the thread's parts."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def offer(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        session_at: int | None,
        lines: Sequence[tuple[str, Change, Sequence[int], Sequence[int]]],
        *,
        now: int,
    ) -> int:
        """Store these lines (thread id, change, line starts, speakers) as waiting; a change already
        offered on that thread, or one with nobody quoted, is skipped. Returns how many are new."""
        new = 0
        async with self._db.guild(guild_id) as conn:
            for thread_id, raw, starts, speakers in lines:
                change = check(raw)
                if change is None or not speakers:
                    continue
                cur = await conn.execute(
                    "INSERT INTO thread_progress (guild_id, campaign_id, id, thread_id, kind,"
                    " part_kind, part_id, words, state, due, change_key, session_id, session_at,"
                    " line_starts, speaker_ids, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (
                        guild_id, campaign_id, new_id(), thread_id, change.kind, change.part_kind,
                        change.part_id, change.words, change.state, change.due,
                        change.key(thread_id), session_id, session_at,
                        sorted({int(n) for n in starts}), sorted({int(n) for n in speakers}),
                        max(now, 1),
                    ),
                )  # fmt: skip
                new += int(cur.rowcount or 0)
        if new:
            log.info("Story thread progress: %d line(s) offered", new)
        return new

    async def waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int] | None = None
    ) -> list[Line]:
        """The waiting lines, oldest first. With `opted_in` (who is recorded in the server now),
        the consent sweep runs first, so no line quoting someone who stopped is ever shown."""
        if opted_in is not None:
            await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM thread_progress WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'waiting' ORDER BY created_at, id",
                (guild_id, campaign_id),
            )
            return [_line(r) for r in await cur.fetchall()]

    async def count(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int] | None = None
    ) -> int:
        """How many lines wait, changing nothing; with `opted_in`, one quoting a speaker no longer
        recorded is not counted (the sweep deletes it when the card is opened)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM thread_progress WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'waiting'"
                " AND (%s::bigint[] IS NULL OR speaker_ids <@ %s::bigint[])",
                (guild_id, campaign_id, *([_ids(opted_in)] * 2)),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row is not None else 0

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Delete the waiting lines with a speaker who is not recorded in this server now: the
        consent re-check on the read path. An answered line is the DM's and stays."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM thread_progress WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'waiting' AND NOT (speaker_ids <@ %s::bigint[])",
                (guild_id, campaign_id, _ids(opted_in)),
            )
            gone = int(cur.rowcount or 0)
        if gone:
            log.info("Story thread progress: %d line(s) dropped (a speaker stopped)", gone)
        return gone

    async def turn_down(self, guild_id: int, campaign_id: str, line_ids: Sequence[str]) -> int:
        """🚫 these lines: never offered again. Returns how many were still waiting."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_progress SET status = 'turned_down' WHERE guild_id = %s"
                " AND campaign_id = %s AND id = ANY(%s) AND status = 'waiting'",
                (guild_id, campaign_id, sorted(set(line_ids))),
            )
            return int(cur.rowcount or 0)

    async def unturn(self, guild_id: int, campaign_id: str, line_ids: Sequence[str]) -> int:
        """↩ Undo of a 🚫 on the card: the lines wait again."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_progress SET status = 'waiting' WHERE guild_id = %s"
                " AND campaign_id = %s AND id = ANY(%s) AND status = 'turned_down'" + _CAN_WAIT,
                (guild_id, campaign_id, sorted(set(line_ids))),
            )
            return int(cur.rowcount or 0)


__all__ = [
    "GONE",
    "KINDS",
    "PART",
    "PART_KINDS",
    "PART_STATE",
    "THREAD_STATE",
    "THREAD_STATES",
    "Change",
    "Line",
    "ThreadProgressStore",
    "check",
    "forget",
    "forget_session",
    "mark_kept",
    "unkeep_in",
    "waiting_in",
]
