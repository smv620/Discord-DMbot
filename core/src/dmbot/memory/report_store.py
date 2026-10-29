"""Storage for the after-session report (#2661; docs/STORY_MEMORY.md 9a): what the party has wrong
(the differences, with PlotBot's sort and reason), the DM's answer to each item, and how far making
a session's report has got.

Three things are kept apart on purpose. The items are made again by each check, and one that is no
longer found is dropped (a connection changed or ended); an answer is a decision the DM made, so it
lives in its own table under a key made from the item's own ids, and a re-found item meets its
answer again ("That's intended" is not raised again; a "remind" shows first next session). Nothing
here holds a transcript word or a speaker: ids, DMbot's own words and numbers. The DM's alone. One
campaign at a time: every query names the campaign, and the tables' row-level security and
composite keys do the rest. Nothing here changes a belief."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory import report_fixed, report_keys
from dmbot.memory.contradiction_grades import one_line
from dmbot.memory.differences import Difference
from dmbot.memory.store import MemoryStore

# (the answer keys live in `report_keys`; the doors and tests use them under these names)
CONTRADICTION, DIFFERENCE = report_keys.CONTRADICTION, report_keys.DIFFERENCE
contradiction_key = report_keys.contradiction_key
difference_key = report_keys.difference_key

DISGUISED, MISUNDERSTOOD, FALSE, NOT_SORTED = "disguised", "misunderstood", "false", "not_sorted"
SORTS = (DISGUISED, MISUNDERSTOOD, FALSE, NOT_SORTED)

INTENDED, NOT_A_PROBLEM, REMIND, FIXED = "intended", "not_a_problem", "remind", "fixed"
ANSWERS = (INTENDED, NOT_A_PROBLEM, REMIND, FIXED)

MAKING, READY, FAILED = "making", "ready", "failed"
STALLED_S = 900  # a run that has not moved for this long is dead (a restart): it may begin again
READING, GRADING, SORTING = "reading", "grading", "sorting"
STAGES = (READING, GRADING, SORTING)


@dataclass(frozen=True, slots=True)
class SortedDifference:
    """A difference and what PlotBot made of it."""

    difference: Difference
    sort: str
    reason: str = ""
    session_started_at: int | None = None


@dataclass(frozen=True, slots=True)
class StoredDifference:
    perspective_id: str
    relation_id: str
    truth_relation_id: str | None
    kind: str
    sort: str
    reason: str
    times_unknown: bool
    involves_guess: bool
    found_at: int
    session_started_at: int | None


@dataclass(frozen=True, slots=True)
class Answer:
    item_key: str
    item_kind: str
    answer: str
    answered_by: int
    answered_at: int
    remind_from: int | None


@dataclass(frozen=True, slots=True)
class Run:
    """How far making one session's report has got: counts and codes only."""

    session_at: int
    state: str
    stage: str
    done: int
    total: int
    started_at: int
    updated_at: int
    # `reason` is a failure code when `state` is failed, and `left_for_next_time` when it is ready
    # but not whole (a call limit left items); empty otherwise.
    reason: str


class ReportStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    @classmethod
    def of(cls, memory: MemoryStore) -> ReportStore:
        """On the memory store's own database (the bot has the one store)."""
        return cls(memory._db)

    # --- the differences --------------------------------------------------------------------

    async def save_differences(
        self, guild_id: int, campaign_id: str, sorted_: Sequence[SortedDifference]
    ) -> int:
        """Keep these sorts. One seen again takes the new sort and reason, except that a sort that
        could not be made (cut, unreadable, refused: `not_sorted`) never replaces one that was.
        One naming a connection or point of view gone since it was found is skipped. How many
        were kept."""
        kept = 0
        found: list[str] = []
        async with self._db.guild(guild_id) as conn:
            for item in sorted_:
                d = item.difference
                sort = item.sort if item.sort in SORTS else NOT_SORTED
                cur = await conn.execute(
                    "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id,"
                    " relation_id, truth_relation_id, kind, sort, reason, times_unknown,"
                    " involves_guess, found_at, session_started_at)"
                    " SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                    " WHERE EXISTS (SELECT 1 FROM memory_perspectives WHERE guild_id = %s"
                    "  AND campaign_id = %s AND id = %s)"
                    " AND (SELECT count(*) FROM memory_relations WHERE guild_id = %s"
                    "  AND campaign_id = %s AND id = ANY(%s)) = %s"
                    " ON CONFLICT (guild_id, campaign_id, perspective_id, relation_id)"
                    " DO UPDATE SET"
                    "  sort = CASE WHEN EXCLUDED.sort = 'not_sorted' THEN"
                    "   memory_differences.sort ELSE EXCLUDED.sort END,"
                    "  reason = CASE WHEN EXCLUDED.sort = 'not_sorted' THEN"
                    "   memory_differences.reason ELSE EXCLUDED.reason END,"
                    "  session_started_at = CASE WHEN EXCLUDED.sort = 'not_sorted' THEN"
                    "   memory_differences.session_started_at"
                    "   ELSE EXCLUDED.session_started_at END,"
                    "  truth_relation_id = EXCLUDED.truth_relation_id, kind = EXCLUDED.kind,"
                    "  times_unknown = EXCLUDED.times_unknown,"
                    "  involves_guess = EXCLUDED.involves_guess",
                    (
                        guild_id, campaign_id, d.perspective_id, d.relation_id,
                        d.truth_relation_id, d.kind, sort, one_line(item.reason),
                        d.times_unknown, d.involves_guess, int(self._now()),
                        item.session_started_at,
                        guild_id, campaign_id, d.perspective_id,
                        guild_id, campaign_id,
                        [d.relation_id, *([d.truth_relation_id] if d.truth_relation_id else [])],
                        2 if d.truth_relation_id else 1,
                    ),
                )  # fmt: skip
                kept += cur.rowcount
                if cur.rowcount:  # found again: a "fixed" answer on it was wrong (#2661)
                    found.append(difference_key(d.perspective_id, d.relation_id))
            await report_fixed.give_back(conn, guild_id, campaign_id, found)
        return kept

    async def differences(
        self, guild_id: int, campaign_id: str, *, sort: str | None = None
    ) -> list[StoredDifference]:
        """The stored differences, the ones with known game times first, then by sort."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT perspective_id, relation_id, truth_relation_id, kind, sort, reason,"
                " times_unknown, involves_guess, found_at, session_started_at"
                " FROM memory_differences WHERE guild_id = %s AND campaign_id = %s"
                " AND (%s::text IS NULL OR sort = %s)"
                " ORDER BY times_unknown, sort, found_at DESC, relation_id, perspective_id",
                (guild_id, campaign_id, sort, sort),
            )
            return [StoredDifference(**dict(r)) for r in await cur.fetchall()]

    async def drop_differences_except(
        self, guild_id: int, campaign_id: str, keep: Collection[tuple[str, str]]
    ) -> int:
        """Forget the sorts of differences no longer found (a belief changed, or The truth now
        agrees): a stale one must not be shown. `keep` is (point of view, connection) pairs; the
        caller passes everything it just found, so an empty one (nothing found) drops them all."""
        views = [v for v, _ in keep]
        relations = [r for _, r in keep]
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_differences d WHERE d.guild_id = %s AND d.campaign_id = %s"
                " AND NOT EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[]) AS k(v, r)"
                "  WHERE k.v = d.perspective_id AND k.r = d.relation_id)",
                (guild_id, campaign_id, views, relations),
            )
            dropped = cur.rowcount
            # (an item no longer found takes its "fixed" answer with it, #2661)
            await report_fixed.forget_unfound(
                conn, guild_id, campaign_id, DIFFERENCE,
                [difference_key(v, r) for v, r in keep],
            )  # fmt: skip
            return dropped

    # --- the DM's answers -------------------------------------------------------------------

    async def answer(
        self,
        guild_id: int,
        campaign_id: str,
        item_key: str,
        item_kind: str,
        answer: str,
        user_id: int,
        *,
        remind_from: int | None = None,
    ) -> bool:
        """Keep the DM's answer to one item, replacing an earlier one. `remind_from` is the start
        of the session whose report was read (only for "remind"). False for an answer or kind that
        does not exist."""
        if answer not in ANSWERS or item_kind not in (CONTRADICTION, DIFFERENCE):
            return False
        if (answer == REMIND) != (remind_from is not None):
            return False
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO report_answers (guild_id, campaign_id, item_key, item_kind, answer,"
                " answered_by, answered_at, remind_from) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, item_key) DO UPDATE SET"
                " item_kind = EXCLUDED.item_kind, answer = EXCLUDED.answer,"
                " answered_by = EXCLUDED.answered_by, answered_at = EXCLUDED.answered_at,"
                " remind_from = EXCLUDED.remind_from, remind_was = NULL",
                (
                    guild_id, campaign_id, item_key, item_kind, answer, user_id,
                    int(self._now()), remind_from,
                ),
            )  # fmt: skip
        return True

    async def undo(self, guild_id: int, campaign_id: str, item_key: str) -> bool:
        """Put an item back to open. False when it had no answer."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM report_answers WHERE guild_id = %s AND campaign_id = %s"
                " AND item_key = %s",
                (guild_id, campaign_id, item_key),
            )
            return cur.rowcount > 0

    async def answers(self, guild_id: int, campaign_id: str) -> dict[str, Answer]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT item_key, item_kind, answer, answered_by, answered_at, remind_from"
                " FROM report_answers WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {r["item_key"]: Answer(**dict(r)) for r in await cur.fetchall()}

    # --- how far making a report has got ----------------------------------------------------

    async def begin(self, guild_id: int, campaign_id: str, session_at: int) -> int | None:
        """Start making this session's report: the run's token (when it started), which every later
        `progress` and `finish` of that run must give, so a run that stalled and was taken over
        cannot overwrite the one that took over. None when one is already being made and has moved
        lately (a second press must not start a second run); one stalled for `STALLED_S` is
        taken over."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
                " done, total, started_at, updated_at, reason)"
                " VALUES (%s, %s, %s, 'making', 'reading', 0, 0, %s, %s, '')"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO UPDATE SET"
                " state = 'making', stage = 'reading', done = 0, total = 0, started_at = %s,"
                " updated_at = %s, reason = ''"
                " WHERE memory_report_runs.state <> 'making'"
                " OR memory_report_runs.updated_at < %s",
                (guild_id, campaign_id, session_at, now, now, now, now, now - STALLED_S),
            )
            return now if cur.rowcount > 0 else None

    async def progress(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        token: int,
        stage: str,
        done: int,
        total: int,
    ) -> None:
        """Say where the run is: counts and a stage code, never a word."""
        if stage not in STAGES:
            raise ValueError("not a stage")
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE memory_report_runs SET stage = %s, done = %s, total = %s, updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND started_at = %s AND state = 'making'",
                (stage, max(done, 0), max(total, 0), int(self._now()), guild_id, campaign_id,
                 session_at, token),
            )  # fmt: skip

    async def finish(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        token: int,
        *,
        failed: str = "",
        left: str = "",
    ) -> None:
        """The run with this token is over: ready, or failed with a code (lower-case letters and
        underscores). `left` is a code for a ready report that is not whole (a call limit left
        items for next time), so the page can say so rather than show it as complete. A run that
        was taken over changes nothing."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE memory_report_runs SET state = %s, updated_at = %s, reason = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND started_at = %s",
                (
                    FAILED if failed else READY, int(self._now()), (failed or left)[:40],
                    guild_id, campaign_id, session_at, token,
                ),
            )  # fmt: skip

    async def run(self, guild_id: int, campaign_id: str, session_at: int) -> Run | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_at, state, stage, done, total, started_at, updated_at, reason"
                " FROM memory_report_runs WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
            return Run(**dict(row)) if row is not None else None
