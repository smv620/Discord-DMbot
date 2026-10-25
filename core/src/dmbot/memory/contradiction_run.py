"""One pass over a campaign's contradiction candidates (#2660): find them, grade the ones not graded
yet, keep the grades (docs/STORY_MEMORY.md 9a). The caller decides whether it may run at all (the
plan, the feature switch, the call limits) and how many calls it may make; this only does the work.

A candidate already graded `major` or `minor` is not asked about again (it would cost a call to say
the same thing); one that is `not_graded` (a cut answer, a failure) is asked again. A candidate
that no longer exists is not graded, and the grade of a clash that is no longer found (a
connection ended or changed) is deleted in every pass, even one that asks nothing, so a stale one
is never told. Nothing here changes a belief, and nothing is told to the DM (that is the next step,
which reads the stored grades)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dmbot.memory import contradiction_grader as grader
from dmbot.memory.contradiction_grades import MAJOR, MINOR, GradeStore
from dmbot.memory.contradictions import Candidate, Contradictions
from dmbot.memory.store import MemoryStore


@dataclass(slots=True)
class Report:
    found: int = 0  # candidates in the campaign now
    asked: int = 0  # put to the AI this time
    major: int = 0
    minor: int = 0
    not_graded: int = 0
    calls: int = 0
    failed: int = 0
    cut: int = 0
    dropped: int = 0  # old grades of clashes no longer found, deleted
    stopped: str = ""

    def line(self) -> str:
        """Counts only, for the server log."""
        return (
            f"Contradictions: found={self.found} asked={self.asked} major={self.major} "
            f"minor={self.minor} not_graded={self.not_graded} calls={self.calls} "
            f"failed={self.failed} cut={self.cut} dropped={self.dropped}"
            + (f" stopped={self.stopped}" if self.stopped else "")
        )


def key(c: Candidate) -> tuple[str, str, str, str]:
    return (c.perspective_id, c.kind, c.first_relation_id, c.second_relation_id)


async def run(
    memory: MemoryStore,
    grades: GradeStore,
    client: grader.Client,
    guild_id: int,
    campaign_id: str,
    *,
    session_started_at: int | None,
    max_calls: int,
    count_call: Callable[[], Awaitable[None]] | None = None,
) -> Report:
    report = Report()
    found = await Contradictions(memory).find(guild_id, campaign_id)
    report.found = len(found)
    report.dropped = await grades.drop_except(guild_id, campaign_id, {key(c) for c in found})
    done = {
        (r.perspective_id, r.kind, r.first_relation_id, r.second_relation_id)
        for r in await grades.rows(guild_id, campaign_id)
        if r.grade in (MAJOR, MINOR)
    }
    todo = [c for c in found if key(c) not in done]
    if not todo or max_calls <= 0:
        return report
    items = await grader.describe(memory, guild_id, campaign_id, todo)
    result = await grader.grade(
        client, items, session_started_at=session_started_at, count_call=count_call,
        max_calls=max_calls,
    )  # fmt: skip
    await grades.save(guild_id, campaign_id, result.graded)
    report.asked, report.calls, report.failed, report.cut = (
        result.asked, result.calls, result.failed, result.cut,
    )  # fmt: skip
    report.stopped = result.stopped
    for g in result.graded:
        if g.grade == MAJOR:
            report.major += 1
        elif g.grade == MINOR:
            report.minor += 1
        else:
            report.not_graded += 1
    return report
