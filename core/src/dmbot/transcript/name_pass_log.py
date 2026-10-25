"""What PlotBot's name pass did to a session, kept for the website (#2684, #2628): the counts the
DM is told and each fix with its Undo number, and whether it was undone. Counts and the campaign's
own list names only. The spellings as heard are NOT copied here: they are read from
`transcript_respells` while a fix stands (they are words from a transcript, for the campaign's DM
only, and they go with the lines they came from)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.transcript.name_pass import Report
from dmbot.transcript.store import TranscriptStore


@dataclass(frozen=True, slots=True)
class StoredFix:
    batch: int
    name: str
    times: int
    made_at: int
    undone_at: int | None


@dataclass(frozen=True, slots=True)
class StoredReport:
    unsure: int
    new_names: int
    guarded: int
    cut: bool
    made_at: int
    fixes: tuple[StoredFix, ...]


class NamePassLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    @classmethod
    def of(cls, transcripts: TranscriptStore) -> NamePassLog:
        return cls(transcripts.db)

    async def record(
        self, guild_id: int, campaign_id: str, session_id: str, report: Report, *, now: int
    ) -> None:
        """Keep a run's counts and fixes. The same session run again (an import) replaces the
        counts and adds its fixes to the same names' (they share one Undo number); a name fixed
        again is no longer undone."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO name_pass_reports (guild_id, campaign_id, session_id, unsure,"
                " new_names, guarded, cut, made_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_id) DO UPDATE SET"
                " unsure = EXCLUDED.unsure, new_names = EXCLUDED.new_names,"
                " guarded = EXCLUDED.guarded, cut = EXCLUDED.cut, made_at = EXCLUDED.made_at",
                (guild_id, campaign_id, session_id, len(report.unsure), len(report.new),
                 report.guarded, report.cut, now),
            )  # fmt: skip
            for fix in report.fixed:
                await conn.execute(
                    "INSERT INTO name_pass_fixes (guild_id, campaign_id, session_id, batch, name,"
                    " times, made_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, session_id, batch) DO UPDATE SET"
                    " name = EXCLUDED.name, times = name_pass_fixes.times + EXCLUDED.times,"
                    " made_at = EXCLUDED.made_at, undone_at = NULL",
                    (guild_id, campaign_id, session_id, fix.batch, fix.name[:200], fix.times, now),
                )

    async def mark_undone(self, guild_id: int, campaign_id: str, batch: int, *, now: int) -> int:
        """This fix was undone (by the DM screen's button or the website). How many rows."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_pass_fixes SET undone_at = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND batch = %s AND undone_at IS NULL",
                (now, guild_id, campaign_id, batch),
            )
            return int(cur.rowcount)

    async def report(self, guild_id: int, campaign_id: str, session_id: str) -> StoredReport | None:
        """The session's run, or None when the pass never ran there (or kept nothing)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT unsure, new_names, guarded, cut, made_at FROM name_pass_reports"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s",
                (guild_id, campaign_id, session_id),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT batch, name, times, made_at, undone_at FROM name_pass_fixes"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s"
                " ORDER BY times DESC, name, batch",
                (guild_id, campaign_id, session_id),
            )
            fixes = tuple(StoredFix(**dict(r)) for r in await cur.fetchall())
        return StoredReport(
            int(row["unsure"]), int(row["new_names"]), int(row["guarded"]), bool(row["cut"]),
            int(row["made_at"]), fixes,
        )  # fmt: skip


def counts(fixes: Sequence[StoredFix]) -> tuple[int, int]:
    """(fixed names, still standing) for a log line."""
    return len(fixes), sum(1 for f in fixes if f.undone_at is None)
