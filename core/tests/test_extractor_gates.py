"""What must be true before the story extractor is switched on (#1367): only a campaign whose plan
includes it runs, a restart never sends the same lines twice, a campaign has a daily ceiling, the
memory is read once per run, and the system text is marked for caching. The AI is canned."""

from __future__ import annotations

import unittest
from collections.abc import Collection
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import AIError
from dmbot.bot import DMBot
from dmbot.campaigns.store import CampaignStore
from dmbot.memory import contradiction_run, extractor
from dmbot.memory.claims import ClaimStore
from dmbot.memory.contradiction_grades import GradeStore
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest
from tests.test_extractor import DM, Canned, Recorder, go, session
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

DAY = 86_400


class Resuming(unittest.IsolatedAsyncioTestCase):
    async def test_lines_that_began_together_are_never_split_across_batches(self) -> None:
        lines = [Line(1000 + (n // 12) * 1000, 7, f"w{n}", f"w{n}") for n in range(30)]
        groups = extractor.batches(extractor.readable(lines))
        sizes = [len(g) for g in groups]
        starts = [g[0].started_ms for g in groups]
        ends = [g[-1].started_ms for g in groups]
        self.assertTrue(all(e < s for e, s in zip(ends, starts[1:], strict=False)), (ends, starts))
        self.assertEqual(sum(sizes), 30)  # nothing lost or doubled

    async def test_a_run_stopped_halfway_and_started_again_sends_every_line_once(self) -> None:
        lines = session(60)
        ai, store = Canned(claim=False), Recorder()
        first = await go(ai, store, lines, max_calls=2)
        self.assertEqual((first.calls, first.stopped), (2, "call limit"))
        self.assertEqual(first.through_ms, lines[19].started_ms)  # the first 20 lines were read
        second = await go(ai, store, lines, after_ms=first.through_ms)
        self.assertEqual(second.stopped, "")
        self.assertEqual(second.through_ms, lines[-1].started_ms)
        sent = "".join(ai.sent)
        for n in range(1, 61):
            self.assertEqual(sent.count(f" words {n}\n"), 1, n)  # each line, exactly once

    async def test_a_batch_that_failed_is_come_back_to_by_the_next_run(self) -> None:
        lines = session(30)
        ai = Canned([None, AIError("x"), None], claim=False)
        first = await go(ai, Recorder(), lines)
        self.assertEqual(first.failed, 1)
        self.assertEqual(first.through_ms, lines[9].started_ms)  # stops at the gap
        again = Canned(claim=False)
        await go(again, Recorder(), lines, after_ms=first.through_ms)
        self.assertIn(" words 11\n", "".join(again.sent))  # the failed ten are sent again
        self.assertNotIn(" words 10\n", "".join(again.sent))

    async def test_a_store_error_means_nothing_counts_as_read(self) -> None:
        class Broken(Recorder):
            async def propose(self, *a: Any, **kw: Any) -> Any:
                raise RuntimeError("boom")

        report = await go(Canned(), Broken(), session(20), after_ms=5)
        self.assertEqual(report.through_ms, 5)

    async def test_every_call_is_counted_as_it_is_made_so_a_crash_cannot_hide_it(self) -> None:
        counted: list[int] = []

        async def count() -> None:
            counted.append(1)

        ai = Canned([None, RuntimeError("the process died")], claim=False)
        report = await go(ai, Recorder(), session(30), count_call=count)
        self.assertEqual(
            (len(counted), report.calls), (3, 3)
        )  # counted before each call, failed or not

    async def test_nothing_new_to_read_makes_no_call(self) -> None:
        lines = session(20)
        ai = Canned()
        report = await go(ai, Recorder(), lines, after_ms=lines[-1].started_ms)
        self.assertEqual((ai.sent, report.calls), ([], 0))


class Ceiling(unittest.TestCase):
    def test_the_calls_left_are_the_smaller_of_the_session_and_the_day(self) -> None:
        self.assertEqual(extractor.calls_left(480, 0, 960, 0), 480)
        self.assertEqual(extractor.calls_left(480, 400, 960, 400), 80)  # the session's own limit
        self.assertEqual(extractor.calls_left(480, 0, 960, 900), 60)  # the day's
        self.assertEqual(extractor.calls_left(480, 480, 960, 500), 0)
        self.assertEqual(extractor.calls_left(480, 500, 960, 1000), 0)  # never below zero


class Progress(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.campaign = await self.campaigns.create(1, "Frostmaiden", DM)

    async def test_progress_is_remembered_per_session_and_never_goes_backwards(self) -> None:
        sid = await self.store.open_session(1, self.campaign.id, 1_700_000_000)
        self.assertEqual(await self.store.extraction(1, sid), (0, 0))
        await self.store.record_extraction(1, sid, through_ms=5000, calls=3, now=100)
        await self.store.record_extraction(1, sid, through_ms=2000, calls=2, now=200)
        self.assertEqual(await self.store.extraction(1, sid), (5000, 5))
        self.assertEqual(await self.store.extraction(1, "f" * 32), (0, 0))  # not a session here

    async def test_a_run_that_made_no_calls_does_not_move_when_the_session_last_ran(self) -> None:
        sid = await self.store.open_session(1, self.campaign.id, 1_700_000_000)
        await self.store.record_extraction(1, sid, through_ms=10, calls=50, now=1_000)
        await self.store.record_extraction(1, sid, through_ms=20, calls=0, now=1_000 + 2 * DAY)
        self.assertEqual(await self.store.extraction(1, sid), (20, 50))
        # Its 50 calls are still counted at the time they were made, not at the later run.
        self.assertEqual(await self.store.extractor_calls_since(1, self.campaign.id, DAY), 0)
        self.assertEqual(await self.store.extractor_calls_since(1, self.campaign.id, 1_000), 50)

    async def test_the_days_calls_count_this_campaign_and_the_last_24_hours_only(self) -> None:
        a = await self.store.open_session(1, self.campaign.id, 1_700_000_000)
        b = await self.store.open_session(1, self.campaign.id, 1_700_100_000)
        await self.store.record_extraction(1, a, through_ms=1, calls=100, now=1_000_000)
        await self.store.record_extraction(1, b, through_ms=1, calls=40, now=1_000_000 + DAY)
        self.assertEqual(
            await self.store.extractor_calls_since(1, self.campaign.id, 1_000_000), 140
        )
        self.assertEqual(await self.store.extractor_calls_since(1, self.campaign.id, 1_000_001), 40)
        other = await self.campaigns.create(1, "Another", DM)
        self.assertEqual(await self.store.extractor_calls_since(1, other.id, 0), 0)
        # Another server sees nothing of it (row-level security).
        self.assertEqual(await self.store.extractor_calls_since(2, self.campaign.id, 0), 0)


class Loading(ConfirmedMemoryTest):
    async def test_the_memory_is_written_once_for_a_run_of_many_batches(self) -> None:
        await self.add("Bram", type="character")
        await self.add("Heartseeker", type="item")
        writes = 0
        real = MemoryStore._write

        def counting(store: MemoryStore, *a: Any, **kw: Any) -> Any:
            nonlocal writes
            writes += 1
            return real(store, *a, **kw)

        async def everyone() -> Collection[int]:
            return {DM, 7}

        ai = Canned()
        with patch.object(MemoryStore, "_write", counting):
            report = await extractor.run(
                ai, ClaimStore(self.memory), session(150, 7), guild_id=GUILD_A, campaign_id=self.c,
                dm_ids={DM}, consenting=everyone, model=lambda: "m", session_started_at=1,
            )  # fmt: skip
        self.assertEqual((report.calls, len(ai.sent)), (15, 15))  # fifteen batches
        self.assertEqual(writes, 1)  # one hand-over, one read of the campaign's memory


class Gates(unittest.IsolatedAsyncioTestCase):
    """`DMBot.extract_claims`, with the parts it asks canned."""

    def fake(self, *, allowed: bool = True, used: tuple[int, int] = (0, 0), day: int = 0) -> Any:
        transcripts = SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(lines=3, started_at=9)),
            lines=AsyncMock(return_value=session(3)),
            extraction=AsyncMock(return_value=used),
            extractor_calls_since=AsyncMock(return_value=day),
            record_extraction=AsyncMock(),
        )
        return SimpleNamespace(
            settings=SimpleNamespace(
                story_extractor_on=True, story_readings_on=False, story_extractor_max_calls=480,
                story_extractor_max_calls_day=960,
            ),
            extractor_ai=object(),
            memory=object(),
            transcripts=transcripts,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c"))),
            plan_allows=AsyncMock(return_value=allowed),
            consent=SimpleNamespace(consenting=AsyncMock(return_value=frozenset({7}))),
            name_of=lambda gid, uid: f"<@{uid}>",
            post=AsyncMock(),
        )  # fmt: skip

    def table(self) -> Any:
        return SimpleNamespace(
            campaign_id="c", transcript_session_id="s", guild_id=1, dm_user_id=DM,
            dm_user_ids=frozenset(), steward_tally=None, screen_channel_id=5,
        )  # fmt: skip

    async def test_a_campaign_whose_plan_has_no_story_memory_runs_nothing(self) -> None:
        me = self.fake(allowed=False)
        with patch.object(extractor, "run", AsyncMock()) as run:
            await DMBot.extract_claims(me, self.table(), 0)
        run.assert_not_called()
        me.plan_allows.assert_awaited_once()
        self.assertEqual(me.plan_allows.await_args.args[0], "story")
        me.transcripts.lines.assert_not_called()  # not even read

    async def test_a_plan_check_that_fails_means_no_run_and_no_progress_noted(self) -> None:
        """The real `plan_allows`, with a meter that raises: closed for the extractor."""
        meter = SimpleNamespace(access=AsyncMock(side_effect=RuntimeError("down")))
        me = self.fake()
        me.settings.enforce_plans = True
        me.meter = meter
        me.plan_allows = lambda *a, **kw: DMBot.plan_allows(me, *a, **kw)
        owned = SimpleNamespace(id="c", owner_user_id=7)
        me.campaigns.get = AsyncMock(return_value=owned)
        with patch.object(extractor, "run", AsyncMock()) as run:
            await DMBot.extract_claims(me, self.table(), 0)
        run.assert_not_called()
        me.transcripts.lines.assert_not_called()
        me.transcripts.record_extraction.assert_not_called()
        # The other plan-gated actions still fail open (unchanged).
        self.assertTrue(await DMBot.plan_allows(me, "ai", 1, owned))  # type: ignore[arg-type]

    async def test_a_campaign_whose_plan_has_it_runs_and_its_progress_is_noted(self) -> None:
        me = self.fake(used=(4000, 30), day=100)
        done = extractor.Report(calls=2, through_ms=7000)
        with patch.object(extractor, "run", AsyncMock(return_value=done)) as run:
            await DMBot.extract_claims(me, self.table(), 0)
        assert run.await_args is not None
        kw = run.await_args.kwargs
        self.assertEqual((kw["after_ms"], kw["max_calls"]), (4000, 450))  # resumes; 480 - 30
        record = me.transcripts.record_extraction.await_args
        assert record is not None
        self.assertEqual(record.kwargs["through_ms"], 7000)
        self.assertEqual(record.kwargs["calls"], 0)  # the calls were counted as they were made
        self.assertIsNotNone(kw["count_call"])

    async def test_the_days_ceiling_stops_a_run_before_it_starts(self) -> None:
        me = self.fake(day=960)
        with patch.object(extractor, "run", AsyncMock()) as run:
            await DMBot.extract_claims(me, self.table(), 0)
        run.assert_not_called()
        me.transcripts.record_extraction.assert_not_called()

    async def test_the_session_limit_counts_calls_before_a_restart(self) -> None:
        me = self.fake(used=(10, 480), day=480)
        with patch.object(extractor, "run", AsyncMock()) as run:
            await DMBot.extract_claims(me, self.table(), 0)
        run.assert_not_called()


class Contradictions(unittest.IsolatedAsyncioTestCase):
    """`DMBot.grade_contradictions`, with the parts it asks canned (#2660)."""

    def fake(self, *, on: bool = True, allowed: bool = True, used: int = 0, day: int = 0) -> Any:
        transcripts = SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(lines=3, started_at=9)),
            extraction=AsyncMock(return_value=(0, used)),
            extractor_calls_since=AsyncMock(return_value=day),
            record_extraction=AsyncMock(),
        )
        return SimpleNamespace(
            settings=SimpleNamespace(
                story_extractor_on=True, story_contradictions_on=on,
                story_extractor_max_calls=480, story_extractor_max_calls_day=960,
            ),
            extractor_ai=object(),
            memory=SimpleNamespace(_db=object()),
            transcripts=transcripts,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c"))),
            plan_allows=AsyncMock(return_value=allowed),
        )  # fmt: skip

    def table(self) -> Any:
        return SimpleNamespace(campaign_id="c", transcript_session_id="s", guild_id=1)

    async def test_off_by_default_runs_nothing_and_reads_nothing(self) -> None:
        me = self.fake(on=False)
        with patch.object(contradiction_run, "run", AsyncMock()) as run:
            await DMBot.grade_contradictions(me, self.table())
        run.assert_not_called()
        me.plan_allows.assert_not_called()

    async def test_without_the_extractor_it_runs_nothing(self) -> None:
        me = self.fake()
        me.settings.story_extractor_on = False
        with patch.object(contradiction_run, "run", AsyncMock()) as run:
            await DMBot.grade_contradictions(me, self.table())
        run.assert_not_called()

    async def test_a_plan_without_story_memory_runs_nothing(self) -> None:
        me = self.fake(allowed=False)
        with patch.object(contradiction_run, "run", AsyncMock()) as run:
            await DMBot.grade_contradictions(me, self.table())
        run.assert_not_called()
        self.assertEqual(me.plan_allows.await_args.args[0], "story")

    async def test_it_runs_with_at_most_three_calls_and_counts_each(self) -> None:
        me = self.fake(used=10, day=100)
        done = contradiction_run.Report(found=2, calls=1)
        with patch.object(contradiction_run, "run", AsyncMock(return_value=done)) as run:
            await DMBot.grade_contradictions(me, self.table())
        assert run.await_args is not None
        kw = run.await_args.kwargs
        self.assertEqual((kw["max_calls"], kw["session_started_at"]), (3, 9))
        await kw["count_call"]()
        me.transcripts.record_extraction.assert_awaited_once()
        self.assertEqual(me.transcripts.record_extraction.await_args.kwargs["calls"], 1)

    async def test_it_makes_only_the_calls_the_extractors_limits_have_left(self) -> None:
        me = self.fake(used=479, day=500)
        with patch.object(
            contradiction_run, "run", AsyncMock(return_value=contradiction_run.Report())
        ) as run:
            await DMBot.grade_contradictions(me, self.table())
        assert run.await_args is not None
        self.assertEqual(run.await_args.kwargs["max_calls"], 1)

    async def test_the_limit_used_up_runs_nothing(self) -> None:
        for me in (self.fake(used=480), self.fake(day=960)):
            with patch.object(contradiction_run, "run", AsyncMock()) as run:
                await DMBot.grade_contradictions(me, self.table())
            run.assert_not_called()


class TellingContradictions(unittest.IsolatedAsyncioTestCase):
    """`DMBot.tell_contradictions` (#2660): the big ones to the DM screen, three at most."""

    def stored(self, n: int, *, grade: str = "major") -> Any:
        return SimpleNamespace(
            perspective_id="p", kind="k", first_relation_id=f"a{n}", second_relation_id=f"b{n}",
            grade=grade, reason=f"Clash {n}", times_unknown=False, involves_guess=False,
        )  # fmt: skip

    def fake(
        self,
        rows: list[Any],
        *,
        on: bool = True,
        allowed: bool = True,
        sent: bool = True,
        visibility: str = "private",
        dm: list[str] | None = None,
    ) -> Any:
        campaign = SimpleNamespace(id="c", dm_screen_visibility=visibility, dm_user_ids=[7, 8])
        return SimpleNamespace(
            settings=SimpleNamespace(story_extractor_on=True, story_contradictions_on=on),
            memory=SimpleNamespace(_db=object()),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            plan_allows=AsyncMock(return_value=allowed),
            post=AsyncMock(return_value=sent),
            sidebar_send_dm=AsyncMock(side_effect=dm or ["sent", "sent"]),
            rows=rows,
        )  # fmt: skip

    def table(self, channel: int | None = 55) -> Any:
        return SimpleNamespace(campaign_id="c", guild_id=1, screen_channel_id=channel)

    async def tell(self, me: Any, table: Any = None) -> Any:
        grades = SimpleNamespace(
            rows=AsyncMock(return_value=me.rows), mark_said=AsyncMock(return_value=0)
        )
        with patch.object(GradeStore, "of", return_value=grades):
            await DMBot.tell_contradictions(me, table or self.table())
        return grades

    async def test_three_of_five_are_told_in_one_private_message_and_marked(self) -> None:
        me = self.fake([self.stored(n) for n in range(5)])
        grades = await self.tell(me)
        me.post.assert_awaited_once()
        args = me.post.await_args
        self.assertEqual(args.args[0], 55)
        self.assertEqual(args.kwargs["secret_of"], (1, "c"))  # pulled back if the screen opens
        self.assertEqual(args.args[1].count("•"), 3)
        self.assertNotIn("Clash 3", args.args[1])  # the rest wait for the report
        told = grades.mark_said.await_args.args[2]
        self.assertEqual([t[2] for t in told], ["a0", "a1", "a2"])
        self.assertEqual(grades.rows.await_args.kwargs, {"grade": "major", "unsaid": True})

    async def test_a_screen_players_may_peek_at_gets_nothing_named_and_the_dms_are_messaged(
        self,
    ) -> None:
        for visibility in ("peek", "open"):
            me = self.fake([self.stored(1)], visibility=visibility)
            grades = await self.tell(me)
            me.post.assert_not_called()  # no clash on the screen the players can open
            self.assertEqual(me.sidebar_send_dm.await_count, 2)  # each DM, privately
            self.assertIn("Clash 1", me.sidebar_send_dm.await_args.args[1])
            grades.mark_said.assert_awaited_once()

    async def test_one_dm_reached_is_enough_to_mark_the_clashes_told(self) -> None:
        me = self.fake([self.stored(1)], visibility="peek", dm=["forbidden", "sent"])
        grades = await self.tell(me)
        grades.mark_said.assert_awaited_once()
        me.post.assert_not_called()

    async def test_closed_private_messages_leave_a_number_on_the_screen_and_tell_nothing(
        self,
    ) -> None:
        me = self.fake([self.stored(1), self.stored(2)], visibility="peek", dm=["forbidden"] * 2)
        grades = await self.tell(me)
        grades.mark_said.assert_not_called()  # tried again next session
        me.post.assert_awaited_once()
        shown = me.post.await_args.args[1]
        self.assertIn("2 clashes", shown)
        self.assertNotIn("Clash", shown.replace("clashes", ""))  # a number only, no line

    async def test_a_send_that_failed_posts_nothing_and_marks_nothing(self) -> None:
        me = self.fake([self.stored(1)], visibility="peek", dm=["failed", "failed"])
        grades = await self.tell(me)
        me.post.assert_not_called()
        grades.mark_said.assert_not_called()

    async def test_nothing_waiting_posts_nothing(self) -> None:
        me = self.fake([])
        grades = await self.tell(me)
        me.post.assert_not_called()
        grades.mark_said.assert_not_called()

    async def test_a_post_that_failed_marks_nothing_so_it_is_tried_again(self) -> None:
        me = self.fake([self.stored(1)], sent=False)
        grades = await self.tell(me)
        me.post.assert_awaited_once()
        grades.mark_said.assert_not_called()

    async def test_off_by_default_closed_plan_or_no_screen_say_nothing(self) -> None:
        for me, table in (
            (self.fake([self.stored(1)], on=False), None),
            (self.fake([self.stored(1)], allowed=False), None),
            (self.fake([self.stored(1)]), self.table(channel=None)),
        ):
            await self.tell(me, table)
            me.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
