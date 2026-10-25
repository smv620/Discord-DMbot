"""What PlotBot made of each contradiction candidate (#2660; docs/STORY_MEMORY.md 9a): its grade,
major or minor, and its one-line reason, kept so a major one is told to the DM once and a minor
one waits for the after-session report.

**Not graded is not minor.** An answer that was cut or unreadable is stored as `not_graded`, so
it is asked again and never shown as the lesser kind. A grade only decides how loudly and when the
DM is told; nothing here changes a belief (the DM decides).

A row holds the ids of the two connections and the point of view that holds both, a grade, a
reason and when it was found: no transcript word and no speaker. The reason is DMbot's own line
about the clash and can name an entry, so it is the DM's alone, read only by the DM-screen and the
DM's report, never by a player's reader (the same as any DM-screen content). One campaign at a time:
every query names the campaign, and the table's row-level security and composite keys do the rest.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory.contradictions import Candidate
from dmbot.memory.store import MemoryStore

MAJOR, MINOR, NOT_GRADED = "major", "minor", "not_graded"
GRADES = (MAJOR, MINOR, NOT_GRADED)
REASON_MAX = 2000  # the column's own limit (a storage limit, not a rule about the answer)


@dataclass(frozen=True, slots=True)
class Graded:
    """A candidate and what PlotBot said of it."""

    candidate: Candidate
    grade: str
    reason: str = ""
    session_started_at: int | None = None


@dataclass(frozen=True, slots=True)
class Stored:
    perspective_id: str
    kind: str
    first_relation_id: str
    second_relation_id: str
    grade: str
    reason: str
    times_unknown: bool
    involves_guess: bool
    found_at: int
    session_started_at: int | None
    said_at: int | None


def one_line(text: str) -> str:
    """A reason as one line of plain words, cut only at the column's own limit (at a word)."""
    line = " ".join(text.split())
    if len(line) <= REASON_MAX:
        return line
    return line[:REASON_MAX].rsplit(" ", 1)[0]


class GradeStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    @classmethod
    def of(cls, memory: MemoryStore) -> GradeStore:
        """On the memory store's own database (the bot has the one store)."""
        return cls(memory._db)

    async def save(self, guild_id: int, campaign_id: str, graded: Sequence[Graded]) -> int:
        """Keep these grades (a candidate seen again takes the new grade and reason, and keeps
        the time a major one was told). One that names a connection or point of view gone since
        it was found is skipped. How many were kept."""
        kept = 0
        async with self._db.guild(guild_id) as conn:
            for g in graded:
                c = g.candidate
                grade = g.grade if g.grade in GRADES else NOT_GRADED
                cur = await conn.execute(
                    "INSERT INTO memory_contradictions (guild_id, campaign_id, perspective_id,"
                    " kind, first_relation_id, second_relation_id, grade, reason, times_unknown,"
                    " involves_guess, found_at, session_started_at)"
                    " SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                    " WHERE EXISTS (SELECT 1 FROM memory_perspectives WHERE guild_id = %s"
                    "  AND campaign_id = %s AND id = %s)"
                    " AND (SELECT count(*) FROM memory_relations WHERE guild_id = %s"
                    "  AND campaign_id = %s AND id IN (%s, %s)) = 2"
                    " ON CONFLICT (guild_id, campaign_id, perspective_id, kind, first_relation_id,"
                    "  second_relation_id) DO UPDATE SET grade = EXCLUDED.grade,"
                    "  reason = EXCLUDED.reason, times_unknown = EXCLUDED.times_unknown,"
                    "  involves_guess = EXCLUDED.involves_guess,"
                    "  session_started_at = EXCLUDED.session_started_at",
                    (
                        guild_id, campaign_id, c.perspective_id, c.kind, c.first_relation_id,
                        c.second_relation_id, grade, one_line(g.reason), c.times_unknown,
                        c.involves_guess, int(self._now()), g.session_started_at,
                        guild_id, campaign_id, c.perspective_id,
                        guild_id, campaign_id, c.first_relation_id, c.second_relation_id,
                    ),
                )  # fmt: skip
                kept += cur.rowcount
        return kept

    async def rows(
        self, guild_id: int, campaign_id: str, *, grade: str | None = None, unsaid: bool = False
    ) -> list[Stored]:
        """The stored grades, the ones with known game times first, newest first. `unsaid`: only
        those not yet told to the DM."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT perspective_id, kind, first_relation_id, second_relation_id, grade, reason,"
                " times_unknown, involves_guess, found_at, session_started_at, said_at"
                " FROM memory_contradictions WHERE guild_id = %s AND campaign_id = %s"
                " AND (%s::text IS NULL OR grade = %s) AND (NOT %s OR said_at IS NULL)"
                " ORDER BY times_unknown, found_at DESC, first_relation_id, second_relation_id",
                (guild_id, campaign_id, grade, grade, unsaid),
            )
            return [Stored(**dict(r)) for r in await cur.fetchall()]

    async def mark_said(
        self, guild_id: int, campaign_id: str, keys: Sequence[tuple[str, str, str, str]]
    ) -> int:
        """These (point of view, kind, first, second) were told to the DM now."""
        told = 0
        async with self._db.guild(guild_id) as conn:
            for perspective, kind, first, second in keys:
                cur = await conn.execute(
                    "UPDATE memory_contradictions SET said_at = %s WHERE guild_id = %s"
                    " AND campaign_id = %s AND perspective_id = %s AND kind = %s"
                    " AND first_relation_id = %s AND second_relation_id = %s AND said_at IS NULL",
                    (int(self._now()), guild_id, campaign_id, perspective, kind, first, second),
                )
                told += cur.rowcount
        return told
