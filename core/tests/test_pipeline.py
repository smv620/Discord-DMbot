import asyncio
import contextlib
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest import mock

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriptionProblem
from dmbot.transcription.pipeline import (
    ALL_CLEAR_AFTER_S,
    BACKLOG_WARN,
    FAILURES_BEFORE_ALERT,
    MIN_CLIP_BUDGET_S,
    SKIP_ALERT_EVERY_S,
    SKIPPED_ALERT,
    SUCCESSES_BEFORE_ALL_CLEAR,
    TranscriptionPipeline,
    clip_budget_s,
)

ONE_SECOND = bytes(32000)


def utt(user: int = 7, pcm: bytes = ONE_SECOND, guild: int = 1, session: int = 0) -> Utterance:
    return Utterance(guild, user, 0, 0, pcm, session)


class FakeConsent:
    def __init__(self, users: set[int]) -> None:
        self.users = users

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        return user_id in self.users


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[tuple[Utterance, list[str]]] = []
        self.fail = False
        self.during_call: list[Callable[[], object]] = []  # run mid-transcription

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        self.calls.append((utterance, hints))
        for hook in self.during_call:
            hook()
        if self.fail:
            raise RuntimeError("engine down")
        return "I cast Shield"

    async def close(self) -> None:
        return None


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.consent = FakeConsent({7})
        self.active = {1}
        self.engine = FakeTranscriber()
        self.delivered: list[tuple[Utterance, str | None]] = []
        self.alerts: list[str] = []

        async def hints(utterance: Utterance) -> list[str]:
            return ["Aria"]

        async def alert(guild_id: int, message: str) -> None:
            self.alerts.append(message)

        self.pipeline = TranscriptionPipeline(
            self.engine,
            self.consent,
            is_active=lambda u: u.guild_id in self.active,
            hints=hints,
            deliver=lambda u, t: self.delivered.append((u, t)),
            alert=alert,
            queue_size=4,
        )

    async def test_transcribes_with_hints(self) -> None:
        await self.pipeline.process(utt())
        self.assertEqual(self.delivered[0][1], "I cast Shield")
        self.assertEqual(self.engine.calls[0][1], ["Aria"])

    async def test_skips_without_consent_or_table(self) -> None:
        await self.pipeline.process(utt(user=99))
        self.active.clear()
        await self.pipeline.process(utt())
        self.assertEqual((self.delivered, self.engine.calls), ([], []))

    async def test_revoke_during_transcription_discards_text(self) -> None:
        self.engine.during_call.append(lambda: self.consent.users.discard(7))
        await self.pipeline.process(utt())
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.delivered, [])

    async def test_table_ending_during_transcription_discards_text(self) -> None:
        self.engine.during_call.append(self.active.clear)
        await self.pipeline.process(utt())
        self.assertEqual(self.delivered, [])

    async def test_short_clip_counted_not_transcribed(self) -> None:
        await self.pipeline.process(utt(pcm=bytes(3200)))  # 0.1 s
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.delivered[0][1], None)

    async def test_failures_alert_once_then_recover(self) -> None:
        self.engine.fail = True
        for _ in range(FAILURES_BEFORE_ALERT + 2):
            await self.pipeline.process(utt())
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Writing things down stopped working", self.alerts[0])
        self.assertIn("hears everyone who said yes", self.alerts[0])  # never "everyone"
        self.assertNotIn("RuntimeError", self.alerts[0])  # details stay in the log (#99)
        self.assertNotIn("engine down", self.alerts[0])
        self.assertTrue(all(text is None for _, text in self.delivered))
        self.engine.fail = False
        await self.pipeline.process(utt())
        self.assertEqual(self.pipeline.consecutive_failures, 0)
        self.assertEqual(len(self.alerts), 1)  # one answer isn't steady yet (#470)
        for _ in range(SUCCESSES_BEFORE_ALL_CLEAR - 1):
            await self.pipeline.process(utt())
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("working again", self.alerts[1])

    async def test_a_flapping_engine_doesnt_churn_the_dm_screen(self) -> None:
        self.engine.fail = True
        for _ in range(FAILURES_BEFORE_ALERT):
            await self.pipeline.process(utt())
        for _ in range(5):  # answer, fail, answer, fail…: never steady
            self.engine.fail = False
            await self.pipeline.process(utt())
            self.engine.fail = True
            await self.pipeline.process(utt())
        self.assertEqual(len(self.alerts), 1)  # just the one "stopped"
        # One answer and quiet for ALL_CLEAR_AFTER_S counts as steady too.
        self.engine.fail = False
        clock = [0.0]
        with mock.patch("dmbot.transcription.pipeline._clock", lambda: clock[0]):
            await self.pipeline.process(utt())
            self.assertEqual(len(self.alerts), 1)
            clock[0] = ALL_CLEAR_AFTER_S + 1  # quiet for a while, then one more answer
            await self.pipeline.process(utt())
        self.assertIn("working again", self.alerts[-1])

    async def test_a_new_session_during_an_outage_is_told_again(self) -> None:
        self.engine.fail = True
        for _ in range(FAILURES_BEFORE_ALERT):
            await self.pipeline.process(utt())
        await self.pipeline.process(utt())
        self.assertEqual(len(self.alerts), 1)
        self.pipeline.session_started(1)  # a new session
        await self.pipeline.process(utt(session=1))
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("stopped working", self.alerts[1])

    async def test_a_plain_engine_problem_is_shown_in_plain_words(self) -> None:
        problem = TranscriptionProblem(
            "Deepgram didn't accept DEEPGRAM_API_KEY (HTTP 401)",
            host_can_fix=True,
            for_dm="Deepgram didn't accept DMbot's key",
        )

        async def fail(utterance: Utterance, hints: list[str]) -> str | None:
            raise problem

        self.engine.transcribe = fail  # type: ignore[method-assign]
        for _ in range(FAILURES_BEFORE_ALERT):
            await self.pipeline.process(utt())
        (alert,) = self.alerts
        self.assertTrue(alert.startswith("⚠️ **No transcript right now:** Deepgram didn't accept"))
        self.assertNotIn("TranscriptionProblem", alert)  # no class names for the DM
        self.assertNotIn("DEEPGRAM_API_KEY", alert)  # nor settings names or codes (#99)
        self.assertNotIn("HTTP", alert)
        self.assertIn("check its speech-to-text settings", alert)
        self.assertNotIn(".env", alert)

    async def test_an_outage_doesnt_send_the_host_to_env(self) -> None:
        async def fail(utterance: Utterance, hints: list[str]) -> str | None:
            raise TranscriptionProblem("Deepgram had a problem (HTTP 503)")

        self.engine.transcribe = fail  # type: ignore[method-assign]
        for _ in range(FAILURES_BEFORE_ALERT):
            await self.pipeline.process(utt())
        (alert,) = self.alerts
        self.assertIn("company's side", alert)
        self.assertIn("You don't need to do anything", alert)  # and it says when it's back
        self.assertNotIn("HTTP", alert)  # no for_dm given: a plain fallback, not the log text
        self.assertNotIn(".env", alert)

    async def test_single_failure_no_alert(self) -> None:
        self.engine.fail = True
        await self.pipeline.process(utt())
        self.engine.fail = False
        await self.pipeline.process(utt())
        self.assertEqual(self.alerts, [])

    async def test_full_queue_counts_drops(self) -> None:
        results = [self.pipeline.enqueue(utt()) for _ in range(6)]
        self.assertEqual(results, [True] * 4 + [False] * 2)
        self.assertEqual((self.pipeline.dropped, self.pipeline.backlog), (2, 4))

    async def test_worker_survives_errors_and_tracks_latency(self) -> None:
        self.engine.fail = True
        task = asyncio.create_task(self.pipeline.run())
        self.pipeline.enqueue(utt())
        self.pipeline.enqueue(utt())
        for _ in range(100):
            if len(self.delivered) == 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        self.assertEqual(len(self.delivered), 2)
        self.assertIsNotNone(self.pipeline.last_latency_s)

    async def test_drain_waits_for_the_last_words(self) -> None:
        # #109: a stopping session waits for its own speech already queued.
        task = asyncio.create_task(self.pipeline.run())
        await asyncio.sleep(0)
        self.pipeline.enqueue(utt(session=5))
        self.pipeline.enqueue(utt(session=5))
        self.assertTrue(await self.pipeline.drain(5, 5))
        self.assertEqual(len(self.delivered), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def stuck_worker(self) -> tuple[asyncio.Task[None], asyncio.Event]:
        release = asyncio.Event()

        async def stuck(utterance: Utterance, hints: list[str]) -> str | None:
            await release.wait()
            return "late"

        self.engine.transcribe = stuck  # type: ignore[method-assign]
        task = asyncio.create_task(self.pipeline.run())
        await asyncio.sleep(0)
        return task, release

    async def test_drain_gives_up_after_its_time_and_the_clip_still_finishes(self) -> None:
        task, release = await self.stuck_worker()
        self.pipeline.enqueue(utt(session=5))
        self.assertFalse(await self.pipeline.drain(5, 0.2))
        release.set()
        for _ in range(50):
            if self.delivered:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.delivered[0][1], "late")
        self.assertEqual(self.pipeline.pending[5], 0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_another_sessions_backlog_is_not_waited_for(self) -> None:
        task, release = await self.stuck_worker()
        self.pipeline.enqueue(utt(guild=2, session=9))  # another server, stuck
        self.assertTrue(await self.pipeline.drain(5, 0.5))  # nothing of ours waiting
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_shutdown_stops_the_wait(self) -> None:
        task, release = await self.stuck_worker()
        self.pipeline.enqueue(utt(session=5))
        waiting = asyncio.create_task(self.pipeline.drain(5, 60))
        await asyncio.sleep(0.05)
        self.pipeline.stop_waiting()
        self.assertFalse(await asyncio.wait_for(waiting, 1))
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_drain_without_a_worker_does_not_wait(self) -> None:
        self.pipeline.enqueue(utt(session=5))
        self.assertFalse(await self.pipeline.drain(5, 30))

    async def test_missed_and_failed_clips_are_counted_per_session(self) -> None:
        for _ in range(6):
            self.pipeline.enqueue(utt(guild=2, session=3))
        self.assertEqual(self.pipeline.missed_in[3], 2)  # the queue holds 4
        self.engine.fail = True
        await self.pipeline.process(utt(session=4))
        self.assertEqual((self.pipeline.failed_in[4], self.pipeline.failed_in[3]), (1, 0))


class BacklogTests(unittest.IsolatedAsyncioTestCase):
    async def test_warns_once_when_falling_behind(self) -> None:
        alerts: list[str] = []

        async def alert(guild_id: int, message: str) -> None:
            alerts.append(message)

        async def hints(utterance: Utterance) -> list[str]:
            return []

        p = TranscriptionPipeline(
            FakeTranscriber(),
            FakeConsent(set()),
            is_active=lambda u: True,
            hints=hints,
            deliver=lambda u, t: None,
            alert=alert,
            queue_size=BACKLOG_WARN * 2,
        )
        for _ in range(BACKLOG_WARN + 1):
            p.enqueue(utt())
        task = asyncio.create_task(p.run())
        for _ in range(100):
            if p.backlog == 0:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        self.assertEqual(len(alerts), 1)
        self.assertIn("falling behind", alerts[0])
        self.assertIn("switch to a faster setting", alerts[0])  # local Whisper
        self.assertNotIn("WHISPER_MODEL", alerts[0])  # settings names stay in the log (#99)

    async def test_with_an_outside_company_the_advice_is_not_about_whisper(self) -> None:
        alerts: list[str] = []

        async def alert(guild_id: int, message: str) -> None:
            alerts.append(message)

        async def hints(utterance: Utterance) -> list[str]:
            return []

        p = TranscriptionPipeline(
            FakeTranscriber(),
            FakeConsent(set()),
            is_active=lambda u: True,
            hints=hints,
            deliver=lambda u, t: None,
            alert=alert,
            queue_size=BACKLOG_WARN * 2,
            outside=True,
        )
        for _ in range(BACKLOG_WARN + 1):
            p.enqueue(utt())
        await p._check_backlog(1)
        self.assertEqual(len(alerts), 1)
        self.assertIn("speech-to-text company is slow", alerts[0])
        self.assertNotIn("WHISPER_MODEL", alerts[0])


class HangingTranscriber(FakeTranscriber):
    """Hangs on the first clip (like Whisper looping on a short clip), then works."""

    def __init__(self) -> None:
        super().__init__()
        self.hang_next = True

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        if self.hang_next:
            self.hang_next = False
            await asyncio.sleep(3600)
        return await super().transcribe(utterance, hints)


class ClipBudgetTests(unittest.IsolatedAsyncioTestCase):
    """#137: one stuck clip must not hold up the ones behind it."""

    async def asyncSetUp(self) -> None:
        self.engine = HangingTranscriber()
        self.delivered: list[tuple[Utterance, str | None]] = []
        self.alerts: list[str] = []

        async def hints(utterance: Utterance) -> list[str]:
            return []

        async def alert(guild_id: int, message: str) -> None:
            self.alerts.append(message)

        self.pipeline = TranscriptionPipeline(
            self.engine,
            FakeConsent({7}),
            is_active=lambda u: True,
            hints=hints,
            deliver=lambda u, t: self.delivered.append((u, t)),
            alert=alert,
            budget_s=lambda duration: 0.5,
        )

    def test_budget_scales_with_clip_length(self) -> None:
        self.assertEqual(clip_budget_s(1.0), MIN_CLIP_BUDGET_S)
        self.assertEqual(clip_budget_s(13.0), 39.0)

    async def test_stuck_clip_is_skipped_and_the_next_one_runs(self) -> None:
        with self.assertLogs("dmbot.transcription.pipeline", "WARNING") as logs:
            await self.pipeline.process(utt())
        await self.pipeline.process(utt())
        # The stuck clip is still delivered (its audio counts), just without text.
        self.assertEqual([t for _, t in self.delivered], [None, "I cast Shield"])
        self.assertEqual(self.pipeline.skipped, 1)
        self.assertEqual(self.alerts, [SKIPPED_ALERT])
        self.assertIn("ran over", logs.output[0])
        self.assertEqual(self.pipeline.consecutive_failures, 0)  # not "engine down"

    async def test_skip_warning_is_throttled(self) -> None:
        for _ in range(3):
            self.engine.hang_next = True
            with self.assertLogs("dmbot.transcription.pipeline", "WARNING"):
                await self.pipeline.process(utt())
        self.assertEqual(self.pipeline.skipped, 3)
        self.assertEqual(self.alerts, [SKIPPED_ALERT])  # once, not three times

    async def test_skip_warning_returns_after_a_while_and_is_per_server(self) -> None:
        now = [1000.0]
        # Only the pipeline's clock: the event loop's must keep running for the budget.
        fake_time = SimpleNamespace(monotonic=lambda: now[0])
        with (
            mock.patch("dmbot.transcription.pipeline.time", fake_time),
            self.assertLogs("dmbot.transcription.pipeline", "WARNING"),
        ):
            await self.pipeline.process(utt(guild=1))
            self.engine.hang_next = True
            await self.pipeline.process(utt(guild=2))  # another server: its own alert
            self.engine.hang_next = True
            await self.pipeline.process(utt(guild=1))  # too soon: no alert
            now[0] += SKIP_ALERT_EVERY_S
            self.engine.hang_next = True
            await self.pipeline.process(utt(guild=1))
        self.assertEqual(self.alerts, [SKIPPED_ALERT] * 3)

    async def test_engine_timeout_is_a_failure_not_a_skip(self) -> None:
        # A cloud request timing out on its own is "not working", not "couldn't keep up".
        async def times_out(utterance: Utterance, hints: list[str]) -> str | None:
            raise TimeoutError("request timed out")

        self.engine.transcribe = times_out  # type: ignore[method-assign]
        with self.assertLogs("dmbot.transcription.pipeline", "ERROR"):
            await self.pipeline.process(utt())
        self.assertEqual((self.pipeline.skipped, self.pipeline.consecutive_failures), (0, 1))
        self.assertEqual(self.alerts, [])

    async def test_name_lookup_error_does_not_stop_transcribing(self) -> None:
        calls = [0]

        async def flaky_hints(utterance: Utterance) -> list[str]:
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("database unavailable")
            return []

        self.engine.hang_next = False
        self.pipeline._hints = flaky_hints
        for _ in range(2):
            self.pipeline.enqueue(utt())
        task = asyncio.create_task(self.pipeline.run())
        try:
            with self.assertLogs("dmbot.transcription.pipeline", "ERROR"):
                for _ in range(200):
                    if len(self.delivered) == 2:
                        break
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
        self.assertEqual([t for _, t in self.delivered], [None, "I cast Shield"])
        self.assertEqual(self.pipeline.total_failures, 1)

    async def test_queue_keeps_moving(self) -> None:
        for _ in range(3):
            self.pipeline.enqueue(utt())
        task = asyncio.create_task(self.pipeline.run())
        try:
            for _ in range(200):
                if len(self.delivered) == 3:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
        self.assertEqual([t for _, t in self.delivered], [None, "I cast Shield", "I cast Shield"])

    def test_skip_message_is_plain(self) -> None:
        # "transcript" is the channel's everyday name; "transcribe" is jargon.
        for jargon in ("whisper", "transcrib", "timeout", "model", "engine"):
            self.assertNotIn(jargon, SKIPPED_ALERT.lower())


class GatedTranscriber(FakeTranscriber):
    """Server 1's clips wait for a gate (a slow table); other servers' answer at once.
    Records how many clips of each server were being written at the same time."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.writing: dict[int, int] = {}
        self.most_at_once: dict[int, int] = {}
        self.order: list[tuple[int, int]] = []  # (server, start_ms) as written

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        guild = utterance.guild_id
        self.writing[guild] = self.writing.get(guild, 0) + 1
        self.most_at_once[guild] = max(self.most_at_once.get(guild, 0), self.writing[guild])
        try:
            if guild == 1:
                await self.gate.wait()
            await asyncio.sleep(0)
            self.order.append((guild, utterance.start_ms))
            return f"line {utterance.start_ms}"
        finally:
            self.writing[guild] -= 1


class WorkerPool(unittest.IsolatedAsyncioTestCase):
    """Several workers, one queue per server (#173)."""

    def make(self, engine: FakeTranscriber, workers: int, queue_size: int = 64) -> Any:
        self.delivered: list[tuple[int, str | None]] = []

        async def hints(utterance: Utterance) -> list[str]:
            return []

        async def alert(guild_id: int, message: str) -> None:
            return None

        return TranscriptionPipeline(
            engine,
            FakeConsent({7}),
            is_active=lambda u: True,
            hints=hints,
            deliver=lambda u, t: self.delivered.append((u.guild_id, t)),
            alert=alert,
            queue_size=queue_size,
            workers=workers,
        )

    @staticmethod
    def clip(guild: int, at_ms: int) -> Utterance:
        return Utterance(guild, 7, at_ms, at_ms + 1000, ONE_SECOND, 0)

    @staticmethod
    async def stop(task: asyncio.Task[None]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def wait_for(self, done: Callable[[], bool]) -> None:
        for _ in range(200):
            if done():
                return
            await asyncio.sleep(0.005)
        self.fail("timed out")

    async def test_a_slow_table_doesnt_hold_up_the_others(self) -> None:
        engine = GatedTranscriber()
        p = self.make(engine, workers=2)
        p.enqueue(self.clip(1, 0))  # server 1 hangs on this one
        for at in (0, 1000, 2000):
            p.enqueue(self.clip(2, at))
        task = asyncio.create_task(p.run())
        await self.wait_for(lambda: len([d for d in self.delivered if d[0] == 2]) == 3)
        self.assertEqual([d for d in self.delivered if d[0] == 1], [])  # still waiting
        engine.gate.set()
        await self.wait_for(lambda: len(self.delivered) == 4)
        await self.stop(task)

    async def test_one_server_is_written_one_clip_at_a_time_in_order(self) -> None:
        engine = GatedTranscriber()
        engine.gate.set()
        p = self.make(engine, workers=3)
        for at in range(0, 10_000, 1000):
            p.enqueue(self.clip(1, at))
            p.enqueue(self.clip(2, at))
        task = asyncio.create_task(p.run())
        await self.wait_for(lambda: len(self.delivered) == 20)
        await self.stop(task)
        self.assertEqual(engine.most_at_once, {1: 1, 2: 1})  # never two at once
        for guild in (1, 2):
            starts = [at for g, at in engine.order if g == guild]
            self.assertEqual(starts, sorted(starts))  # lines never swap

    async def test_servers_take_turns(self) -> None:
        engine = GatedTranscriber()
        engine.gate.set()
        p = self.make(engine, workers=1)
        for at in range(3):
            p.enqueue(self.clip(1, at))
        p.enqueue(self.clip(2, 0))
        task = asyncio.create_task(p.run())
        await self.wait_for(lambda: len(self.delivered) == 4)
        await self.stop(task)
        # Server 2's one clip doesn't wait behind all of server 1's.
        self.assertEqual([g for g, _ in engine.order], [1, 2, 1, 1])

    async def test_each_server_has_its_own_queue(self) -> None:
        p = self.make(FakeTranscriber(), workers=1, queue_size=2)
        results = [p.enqueue(self.clip(1, at)) for at in range(3)]
        self.assertEqual(results, [True, True, False])  # server 1 is full
        self.assertTrue(p.enqueue(self.clip(2, 0)))  # server 2 still has room
        self.assertEqual((p.dropped, p.backlog, p.backlog_of(1), p.backlog_of(2)), (1, 3, 2, 1))

    async def test_a_cap_across_all_servers_bounds_memory(self) -> None:
        p = self.make(FakeTranscriber(), workers=1, queue_size=10)
        p._max_queued = 3
        added = [p.enqueue(self.clip(guild, 0)) for guild in (1, 2, 3, 4)]
        self.assertEqual(added, [True, True, True, False])  # server 4 is refused too
        self.assertEqual(p.dropped, 1)

    async def test_a_clip_arriving_while_its_server_is_written_waits_its_turn(self) -> None:
        engine = GatedTranscriber()
        p = self.make(engine, workers=3)
        task = asyncio.create_task(p.run())
        p.enqueue(self.clip(1, 0))
        await self.wait_for(lambda: engine.writing.get(1) == 1)
        p.enqueue(self.clip(1, 1000))  # server 1 is being written: no second worker
        await asyncio.sleep(0.02)
        self.assertEqual(engine.most_at_once[1], 1)
        engine.gate.set()
        await self.wait_for(lambda: len(self.delivered) == 2)
        await self.stop(task)
        self.assertEqual([at for _, at in engine.order], [0, 1000])
        # Nothing left behind once a server's speech is all written.
        self.assertEqual((p._queues, p._writing, p.backlog), ({}, set(), 0))
        self.assertIn(1, p.latency_of)

    async def test_one_clip_failing_badly_never_stops_a_worker(self) -> None:
        p = self.make(FakeTranscriber(), workers=1)
        calls = []

        def deliver(u: Utterance, text: str | None) -> None:
            calls.append(u.start_ms)
            if u.start_ms == 0:
                raise RuntimeError("bug in delivery")

        p._deliver = deliver
        for at in (0, 1000):
            p.enqueue(self.clip(1, at))
        task = asyncio.create_task(p.run())
        with self.assertLogs("dmbot.transcription.pipeline", "ERROR"):
            await self.wait_for(lambda: len(calls) == 2)
        self.assertTrue(p._running)  # the worker is still there
        await self.stop(task)

    async def test_drain_waits_for_its_own_session_only(self) -> None:
        engine = GatedTranscriber()  # server 1 is held at the gate
        p = self.make(engine, workers=3)
        p.enqueue(self.clip(1, 0))
        mine = Utterance(2, 7, 0, 1000, ONE_SECOND, 5)
        p.enqueue(mine)
        task = asyncio.create_task(p.run())
        await asyncio.sleep(0)  # let the workers start
        self.assertTrue(await p.drain(5, timeout_s=2))
        self.assertEqual(p.pending[0], 1)  # server 1's clip still waits
        engine.gate.set()
        await self.stop(task)

    async def test_every_busy_server_hears_that_writing_stopped_and_came_back(self) -> None:
        engine = FakeTranscriber()
        engine.fail = True
        p = self.make(engine, workers=1)
        told: list[tuple[int, str]] = []

        async def alert(guild_id: int, message: str) -> None:
            told.append((guild_id, message))

        p._alert = alert
        for at in range(FAILURES_BEFORE_ALERT):
            p.enqueue(self.clip(1, at * 1000))
        p.enqueue(self.clip(2, 0))
        p.enqueue(self.clip(2, 1000))
        task = asyncio.create_task(p.run())
        await self.wait_for(lambda: p.backlog == 0 and not p._writing)
        stopped = [g for g, m in told if "stopped" in m or "No transcript" in m]
        self.assertEqual(sorted(stopped), [1, 2])  # both tables, once each
        engine.fail = False
        for at in range(SUCCESSES_BEFORE_ALL_CLEAR):  # steady: "working again" (#470)
            p.enqueue(self.clip(2, 2000 + at * 1000))
        await self.wait_for(lambda: any("working again" in m for _, m in told))
        await self.stop(task)
        self.assertEqual(sorted(g for g, m in told if "working again" in m), [1, 2])

    async def test_stopping_with_clips_in_flight_leaves_nothing_half_done(self) -> None:
        engine = GatedTranscriber()  # server 1 hangs mid-clip
        p = self.make(engine, workers=2)
        p.enqueue(self.clip(1, 0))
        p.enqueue(self.clip(1, 1000))
        p.enqueue(self.clip(2, 0))
        task = asyncio.create_task(p.run())
        await self.wait_for(lambda: engine.writing.get(1) == 1 and len(self.delivered) == 1)
        await self.stop(task)  # shutting down mid-clip
        self.assertFalse(p._running)
        self.assertEqual(p._writing, set())  # nothing claims to be in progress
        self.assertEqual(p.pending[0], 1)  # the clip never started is still counted
        self.assertEqual(p.backlog_of(1), 1)  # still queued, and its turn is still there
        self.assertIn(1, list(p._turns._queue))

    async def test_an_ended_sessions_last_clips_dont_block_telling_the_next(self) -> None:
        engine = FakeTranscriber()
        engine.fail = True
        p = self.make(engine, workers=1)
        told: list[tuple[int, str]] = []

        async def alert(guild_id: int, message: str) -> None:
            told.append((guild_id, message))

        p._alert = alert
        task = asyncio.create_task(p.run())
        for at in range(FAILURES_BEFORE_ALERT):  # an outage: told once
            p.enqueue(self.clip(1, at * 1000))
        await self.wait_for(lambda: len(told) == 1)
        p.enqueue(self.clip(1, 9000))  # the stopped session's last words, still failing
        await self.wait_for(lambda: p.backlog == 0 and not p._writing)
        p.session_started(1)  # a new /dmbot start during the same outage
        p.enqueue(self.clip(1, 20_000))
        await self.wait_for(lambda: len(told) == 2)
        await self.stop(task)
        self.assertEqual([g for g, _ in told], [1, 1])  # the new session is told too

    async def test_a_problem_posting_an_alert_never_loses_the_clip(self) -> None:
        engine = FakeTranscriber()
        engine.fail = True
        p = self.make(engine, workers=1)

        async def broken(guild_id: int, message: str) -> None:
            raise RuntimeError("Discord said no")

        p._alert = broken
        for _ in range(FAILURES_BEFORE_ALERT):
            await p.process(self.clip(1, 0))
        engine.fail = False
        with self.assertLogs("dmbot.transcription.pipeline", "ERROR"):
            for at in range(SUCCESSES_BEFORE_ALL_CLEAR):
                await p.process(self.clip(1, at))
        self.assertEqual(len([t for _, t in self.delivered if t]), SUCCESSES_BEFORE_ALL_CLEAR)

    async def test_a_revoke_while_waiting_its_turn_means_it_isnt_written(self) -> None:
        engine = GatedTranscriber()
        consent = FakeConsent({7})
        p = self.make(engine, workers=1)
        p._consent = consent
        p.enqueue(self.clip(1, 0))  # holds the only worker
        waiting = Utterance(2, 7, 0, 1000, ONE_SECOND, 0)
        p.enqueue(waiting)
        task = asyncio.create_task(p.run())
        await self.wait_for(lambda: engine.writing.get(1) == 1)
        consent.users.discard(7)  # stops being recorded while queued
        engine.gate.set()
        await self.wait_for(lambda: p.backlog == 0 and not p._writing)
        await self.stop(task)
        self.assertNotIn(2, [g for g, _ in engine.order])  # never sent to be written

    async def test_a_stalled_alert_post_never_holds_up_other_tables(self) -> None:
        # The engine is down for everyone. Server 1's "stopped" post hangs in Discord
        # while server 2 fails too: server 2 is still told, soon (#499, #505).
        engine = FakeTranscriber()
        engine.fail = True
        p = self.make(engine, workers=2)
        never = asyncio.Event()
        posted: list[int] = []

        async def alert(guild_id: int, message: str) -> None:
            posted.append(guild_id)
            if guild_id == 1:
                await never.wait()  # Discord hangs on this one

        p._alert = alert
        for at in range(FAILURES_BEFORE_ALERT):
            p.enqueue(self.clip(1, at * 1000))
        task = asyncio.create_task(p.run())
        with (
            mock.patch("dmbot.transcription.pipeline.ALERT_POST_TIMEOUT_S", 0.3),
            self.assertLogs("dmbot.transcription.pipeline", "WARNING") as logs,
        ):
            await self.wait_for(lambda: posted == [1])  # stalled, holding the lock
            p.enqueue(self.clip(2, 0))  # server 2 fails meanwhile
            started = asyncio.get_running_loop().time()
            await self.wait_for(lambda: 2 in posted)
            waited = asyncio.get_running_loop().time() - started
            p.enqueue(self.clip(1, 9000))  # server 1 fails again: not told twice
            await self.wait_for(lambda: p.backlog == 0 and not p._writing)
        await self.stop(task)
        self.assertLess(waited, 1.0)  # about one post's time, not forever
        self.assertEqual(posted, [1, 2])  # each told once
        self.assertEqual(p._told_stopped, {1, 2})  # the post that timed out still counts
        self.assertIn("took too long", "\n".join(logs.output))

    async def test_a_turn_for_a_server_with_no_queue_is_a_warning(self) -> None:
        p = self.make(FakeTranscriber(), workers=1)
        p._turns.put_nowait(9)  # no queue for server 9
        p.enqueue(self.clip(1, 0))
        task = asyncio.create_task(p.run())
        with self.assertLogs("dmbot.transcription.pipeline", "WARNING") as logs:
            await self.wait_for(lambda: len(self.delivered) == 1)  # the worker carries on
        await self.stop(task)
        self.assertIn("empty turn", "\n".join(logs.output))
        self.assertNotIn("Traceback", "\n".join(logs.output))

    async def test_two_tables_one_told_and_one_starting_mid_outage(self) -> None:
        engine = FakeTranscriber()
        engine.fail = True
        p = self.make(engine, workers=1)
        told: list[int] = []

        async def alert(guild_id: int, message: str) -> None:
            told.append(guild_id)

        p._alert = alert
        for _ in range(FAILURES_BEFORE_ALERT):
            await p.process(self.clip(1, 0))
        self.assertEqual(told, [1])
        p.session_started(2)  # a new table, mid-outage
        await p.process(self.clip(2, 0))
        await p.process(self.clip(1, 0))  # the first table fails again
        self.assertEqual(told, [1, 2])  # the new one is told; the first not twice

    async def test_the_backlog_warning_is_per_server(self) -> None:
        alerts: list[int] = []

        async def alert(guild_id: int, message: str) -> None:
            alerts.append(guild_id)

        p = self.make(FakeTranscriber(), workers=1, queue_size=BACKLOG_WARN * 2)
        p._alert = alert
        for at in range(BACKLOG_WARN):
            p.enqueue(self.clip(1, at))
        p.enqueue(self.clip(2, 0))
        await p._check_backlog(2)  # server 2 has 1 waiting: no warning for it
        await p._check_backlog(1)
        await p._check_backlog(1)  # once, not again
        self.assertEqual(alerts, [1])
