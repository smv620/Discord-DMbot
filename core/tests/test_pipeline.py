import asyncio
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriptionProblem
from dmbot.transcription.pipeline import (
    BACKLOG_WARN,
    FAILURES_BEFORE_ALERT,
    MIN_CLIP_BUDGET_S,
    SKIP_ALERT_EVERY_S,
    SKIPPED_ALERT,
    TranscriptionPipeline,
    clip_budget_s,
)

ONE_SECOND = bytes(32000)


def utt(user: int = 7, pcm: bytes = ONE_SECOND, guild: int = 1) -> Utterance:
    return Utterance(guild, user, 0, 0, pcm)


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

        async def hints(guild_id: int) -> list[str]:
            return ["Aria"]

        async def alert(guild_id: int, message: str) -> None:
            self.alerts.append(message)

        self.pipeline = TranscriptionPipeline(
            self.engine,
            self.consent,
            is_active=lambda g: g in self.active,
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
        self.assertIn("isn't working", self.alerts[0])
        self.assertTrue(all(text is None for _, text in self.delivered))
        self.engine.fail = False
        await self.pipeline.process(utt())
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("working again", self.alerts[1])
        self.assertEqual(self.pipeline.consecutive_failures, 0)

    async def test_a_plain_engine_problem_is_shown_in_plain_words(self) -> None:
        problem = TranscriptionProblem(
            "Deepgram didn't accept DEEPGRAM_API_KEY (HTTP 401)", host_can_fix=True
        )

        async def fail(utterance: Utterance, hints: list[str]) -> str | None:
            raise problem

        self.engine.transcribe = fail  # type: ignore[method-assign]
        for _ in range(FAILURES_BEFORE_ALERT):
            await self.pipeline.process(utt())
        (alert,) = self.alerts
        self.assertTrue(alert.startswith("⚠️ **No transcript right now:** Deepgram didn't accept"))
        self.assertNotIn("TranscriptionProblem", alert)  # no class names for the DM
        self.assertIn("check the speech-to-text settings in .env", alert)

    async def test_an_outage_doesnt_send_the_host_to_env(self) -> None:
        async def fail(utterance: Utterance, hints: list[str]) -> str | None:
            raise TranscriptionProblem("Deepgram had a problem (HTTP 503)")

        self.engine.transcribe = fail  # type: ignore[method-assign]
        for _ in range(FAILURES_BEFORE_ALERT):
            await self.pipeline.process(utt())
        (alert,) = self.alerts
        self.assertIn("company's side", alert)
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


class BacklogTests(unittest.IsolatedAsyncioTestCase):
    async def test_warns_once_when_falling_behind(self) -> None:
        alerts: list[str] = []

        async def alert(guild_id: int, message: str) -> None:
            alerts.append(message)

        async def hints(guild_id: int) -> list[str]:
            return []

        p = TranscriptionPipeline(
            FakeTranscriber(),
            FakeConsent(set()),
            is_active=lambda g: True,
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
        self.assertIn("WHISPER_MODEL", alerts[0])  # local Whisper: a smaller model helps

    async def test_with_an_outside_company_the_advice_is_not_about_whisper(self) -> None:
        alerts: list[str] = []

        async def alert(guild_id: int, message: str) -> None:
            alerts.append(message)

        async def hints(guild_id: int) -> list[str]:
            return []

        p = TranscriptionPipeline(
            FakeTranscriber(),
            FakeConsent(set()),
            is_active=lambda g: True,
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

        async def hints(guild_id: int) -> list[str]:
            return []

        async def alert(guild_id: int, message: str) -> None:
            self.alerts.append(message)

        self.pipeline = TranscriptionPipeline(
            self.engine,
            FakeConsent({7}),
            is_active=lambda g: True,
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

        async def flaky_hints(guild_id: int) -> list[str]:
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
