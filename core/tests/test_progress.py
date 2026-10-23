"""The shared job progress shape (#2564): stage, counts, times, and when a job is stalled."""

from __future__ import annotations

import json
import unittest

from dmbot import progress as pg

NOW = 10_000


def one(stage: str, updated: int, *, started: int = 1000, **more: object) -> dict[str, object]:
    return pg.shape(stage, started_at=started, updated_at=updated, now=NOW, **more)  # type: ignore[arg-type]


class TheShape(unittest.TestCase):
    def test_a_job_that_moved_lately_is_running_with_its_counts(self) -> None:
        got = one("cleaning", NOW - 5, done=420, total=900, unit="lines")
        self.assertEqual(
            got,
            {
                "stage": "cleaning", "startedAt": 1000, "updatedAt": NOW - 5, "serverNow": NOW,
                "state": "running", "reason": None, "done": 420, "total": 900, "unit": "lines",
            },
        )  # fmt: skip

    def test_counts_are_left_out_when_not_known_and_never_pass_the_total(self) -> None:
        self.assertNotIn("done", one("checking_names", NOW))
        self.assertNotIn("done", one("cleaning", NOW, done=0, total=0))
        self.assertEqual(one("cleaning", NOW, done=12, total=10)["done"], 10)

    def test_no_move_for_longer_than_the_stage_limit_is_stalled_with_plain_words(self) -> None:
        late = NOW - pg.STALL_AFTER_S["cleaning"] - 1  # type: ignore[operator]
        got = one("cleaning", late)
        self.assertEqual((got["state"], got["reason"]), ("stalled", pg.STALLED_REASON))
        self.assertEqual(one("cleaning", late + 2)["state"], "running")

    def test_a_stage_that_waits_for_a_person_never_stalls(self) -> None:
        self.assertEqual(one("waiting_for_names", 1001)["state"], "running")

    def test_a_failed_job_carries_its_reason_and_a_done_one_none(self) -> None:
        failed = one("failed", NOW, reason="no names")
        self.assertEqual((failed["state"], failed["reason"]), ("failed", "no names"))
        self.assertEqual(one("recap_failed", NOW)["reason"], pg.RECAP_FAILED_REASON)
        done = one("recap_ready", NOW, reason="leftover")
        self.assertEqual((done["state"], done["reason"]), ("done", None))

    def test_a_job_waiting_to_be_taken_stalls_only_after_an_hour(self) -> None:
        hours_ago = NOW - 30 * 60
        self.assertEqual(one("cleaning", hours_ago, queued=True)["state"], "running")
        self.assertEqual(one("cleaning", hours_ago)["state"], "stalled")
        self.assertEqual(one("cleaning", NOW - 3601, queued=True)["state"], "stalled")

    def test_a_recap_that_was_held_or_declined_is_an_end_not_a_stall(self) -> None:
        got = one("recap_not_written", 1000)
        self.assertEqual(got["state"], "done")
        whole = pg.whole([got, one("done", NOW)], now=NOW)
        self.assertEqual((whole["stage"], whole["state"]), ("done", "done"))

    def test_updated_never_goes_after_now(self) -> None:
        self.assertEqual(one("cleaning", NOW + 50)["updatedAt"], NOW)

    def test_updated_never_goes_before_started(self) -> None:
        self.assertEqual(one("cleaning", 0)["updatedAt"], 1000)

    def test_only_codes_counts_and_words_are_in_it(self) -> None:
        text = json.dumps(one("cleaning", NOW, done=1, total=2, unit="lines"))
        self.assertNotIn("Craig", text)


class TheWholeImport(unittest.TestCase):
    def test_it_is_at_the_earliest_unfinished_stage_with_sessions_counted(self) -> None:
        parts = [one("recap_ready", NOW), one("checking_names", NOW - 3), one("writing_recap", NOW)]
        got = pg.whole(parts, now=NOW)
        self.assertEqual(
            (got["stage"], got["state"], got["done"], got["total"], got["unit"]),
            ("checking_names", "running", 1, 3, "sessions"),
        )
        self.assertEqual((got["startedAt"], got["updatedAt"]), (1000, NOW))

    def test_a_failed_or_stalled_session_shows_on_the_whole(self) -> None:
        stalled = one("cleaning", 1000)
        failed = one("failed", NOW, reason="x")
        self.assertEqual(pg.whole([one("done", NOW), stalled], now=NOW)["state"], "stalled")
        got = pg.whole([stalled, failed], now=NOW)
        self.assertEqual((got["state"], got["reason"]), ("failed", "x"))

    def test_all_done_is_done_and_all_ended_with_a_failure_is_failed(self) -> None:
        self.assertEqual(pg.whole([one("done", NOW)], now=NOW)["stage"], "done")
        got = pg.whole([one("done", NOW), one("failed", NOW, reason="y")], now=NOW)
        self.assertEqual((got["stage"], got["state"]), ("failed", "failed"))
