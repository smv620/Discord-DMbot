import asyncio
import unittest
from collections.abc import Callable

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.pipeline import (
    BACKLOG_WARN,
    FAILURES_BEFORE_ALERT,
    TranscriptionPipeline,
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
