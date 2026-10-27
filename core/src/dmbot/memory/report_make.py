"""Making one session's after-session report (#2661; docs/STORY_MEMORY.md 9a): grade the clashes not
graded yet (the contradiction pass), find what the party has wrong and sort it (PlotBot's sort),
keeping what each step makes, and say how far it has got for the website's progress line.

The caller (the bot, when the DM asks on the website) decides whether it may run at all (the plan,
its switch) and how many AI calls it may make (`max_calls`, counted by `count_call` before each is
made). What is kept is read by the report's doors; nothing here changes a belief, tells a player
anything, or holds a transcript word: the AI is shown the campaign's own names and relationship
words only, and the progress is a stage code and two counts.

One run at a time for a session (`ReportStore.begin`); a run that stalled is taken over, and the
old one can then no longer change the run's state or progress (its kept grades and sorts are
idempotent, so a late write only repeats work). A step that could not finish (a call limit, a cut
or refused answer) leaves its items ungraded or unsorted, asked again next time, and the report is
still ready, marked `left_for_next_time` so the page never shows it as whole. Only an out-of-funds
stop or a failure is reported as failed, with a code."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.memory import contradiction_grader as grader
from dmbot.memory import contradiction_run as grading
from dmbot.memory import difference_sorter as sorter
from dmbot.memory.contradiction_grades import GradeStore
from dmbot.memory.differences import Differences
from dmbot.memory.report_store import (
    GRADING,
    NOT_SORTED,
    READING,
    SORTING,
    ReportStore,
)
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)

OUT_OF_FUNDS = "out_of_funds"  # the failure codes the website has words for
FAILED = "failed"
LEFT = "left_for_next_time"  # a ready report with grades or sorts still to do
BUSY = "busy"  # not a failure: a run for this session is already going


class Client(grader.Client, sorter.Client, Protocol):
    """What the two steps need from a tier client (the same call)."""


@dataclass(slots=True)
class Made:
    ran: bool = False  # False: another run was already going
    failed: str = ""  # a failure code, or ""
    graded: grading.Report | None = None
    differences: int = 0  # found now
    sorted: int = 0  # given a real sort this time
    not_sorted: int = 0  # found, and still without a sort (asked or not)
    calls: int = 0

    def line(self) -> str:
        """Counts only, for the server log."""
        if not self.ran:
            return "Report: not started, one is already being made"
        g = self.graded
        return (
            f"Report: contradictions found={g.found if g else 0} major={g.major if g else 0} "
            f"minor={g.minor if g else 0} not_graded={g.not_graded if g else 0} "
            f"left={g.left if g else 0}; "
            f"differences={self.differences} sorted={self.sorted} not_sorted={self.not_sorted}; "
            f"calls={self.calls}" + (f" failed={self.failed}" if self.failed else "")
        )


async def make(
    memory: MemoryStore,
    grades: GradeStore,
    reports: ReportStore,
    client: Client,
    guild_id: int,
    campaign_id: str,
    session_at: int,
    *,
    session_started_at: int | None,
    max_calls: int,
    count_call: Callable[[], Awaitable[None]] | None = None,
) -> Made:
    made = Made()
    left = ""
    token = await reports.begin(guild_id, campaign_id, session_at)
    if token is None:
        return made
    made.ran = True

    async def progress(stage: str, done: int, total: int) -> None:
        # A display only: a failed write must not throw away the batches already paid for.
        try:
            await reports.progress(guild_id, campaign_id, session_at, token, stage, done, total)
        except Exception as exc:
            log.warning("Report: progress not saved (%s)", type(exc).__name__)

    try:
        await progress(READING, 0, 1)
        found = await Differences(memory).find(guild_id, campaign_id)
        made.differences = len(found)
        await progress(READING, 1, 1)

        async def grading_progress(done: int, total: int) -> None:
            await progress(GRADING, done, total)

        await progress(GRADING, 0, 0)
        made.graded = await grading.run(
            memory, grades, client, guild_id, campaign_id,
            session_started_at=session_started_at, max_calls=max_calls, count_call=count_call,
            on_progress=grading_progress,
        )  # fmt: skip
        made.calls = made.graded.calls
        stopped = made.graded.stopped

        # The sorts of differences no longer found are dropped; a difference already sorted is not
        # asked about again (a `not_sorted` one is).
        await reports.drop_differences_except(
            guild_id, campaign_id, {(d.perspective_id, d.relation_id) for d in found}
        )
        sorted_already = {
            (s.perspective_id, s.relation_id)
            for s in await reports.differences(guild_id, campaign_id)
            if s.sort != NOT_SORTED
        }
        todo = [d for d in found if (d.perspective_id, d.relation_id) not in sorted_already]
        if todo and not stopped:
            items = await sorter.describe(memory, guild_id, campaign_id, todo)

            async def sorting_progress(done: int, total: int) -> None:
                await progress(SORTING, done, total)

            await progress(SORTING, 0, len(items))
            result = await sorter.sort(
                client, items, session_started_at=session_started_at,
                max_calls=max(max_calls - made.calls, 0), count_call=count_call,
                on_progress=sorting_progress,
            )  # fmt: skip
            await reports.save_differences(guild_id, campaign_id, result.sorted)
            made.sorted = sum(1 for s in result.sorted if s.sort != NOT_SORTED)
            made.calls += result.calls
            stopped = result.stopped
        # Differences never put to the AI (a call limit) are not stored, so count from `found`.
        real = {
            (s.perspective_id, s.relation_id)
            for s in await reports.differences(guild_id, campaign_id)
            if s.sort != NOT_SORTED
        }
        made.not_sorted = sum(1 for d in found if (d.perspective_id, d.relation_id) not in real)
        made.failed = OUT_OF_FUNDS if stopped else ""
        if not made.failed and (made.not_sorted or (made.graded and made.graded.left)):
            left = LEFT
    except Exception as exc:  # never fatal: the run is reported failed, with a code and no words
        log.warning("Report: making it failed (%s)", type(exc).__name__)
        made.failed = FAILED
    except asyncio.CancelledError:
        # A restart or shutdown in the middle: the run is closed as failed now, so the page does not
        # show "making" for a quarter of an hour (the takeover after STALLED_S covers a hard kill).
        await asyncio.shield(
            reports.finish(guild_id, campaign_id, session_at, token, failed=FAILED)
        )
        raise
    await reports.finish(guild_id, campaign_id, session_at, token, failed=made.failed, left=left)
    return made
