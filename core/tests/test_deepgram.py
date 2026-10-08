"""Deepgram Nova-3 engine (#170) against a local fake of Deepgram's pre-recorded API."""

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.audio.segmenter import Utterance
from dmbot.transcription import deepgram as dg
from dmbot.transcription.config import TranscriptionSettings
from dmbot.transcription.deepgram import DeepgramError, DeepgramTranscriber
from dmbot.transcription.factory import build_transcriber


def reply(text: str) -> dict[str, Any]:
    return {"results": {"channels": [{"alternatives": [{"transcript": text}]}]}}


def clip(guild_id: int = 1, session: int = 0) -> Utterance:
    return Utterance(guild_id, 2, 0, 0, bytes(3200), session=session)


class KeytermTests(unittest.TestCase):
    def test_tidied_deduplicated_and_ordered(self) -> None:
        self.assertEqual(
            dg.keyterms(["  Bryn   Shander ", "Auril", "auril", "", "Caer-Dineval"]),
            ["Bryn Shander", "Auril", "Caer-Dineval"],
        )

    def test_capped(self) -> None:
        many = [f"Name{i}" for i in range(200)]
        self.assertEqual(len(dg.keyterms(many)), dg.MAX_KEYTERMS)
        long = ["x" * 250, "y" * 250, "z" * 250]
        self.assertEqual(dg.keyterms(long), ["x" * 250, "y" * 250])  # third goes over 600
        self.assertEqual(dg.keyterms(long, max_chars=300), ["x" * 250])
        # One long name doesn't push out the shorter ones after it.
        self.assertEqual(dg.keyterms(["x" * 150, "Auril"], max_chars=100), ["Auril"])

    def test_fallbacks_halve_then_drop_keyterms(self) -> None:
        self.assertEqual(dg.fallbacks(["a", "b", "c"]), [["a", "b", "c"], ["a"], []])
        self.assertEqual(dg.fallbacks(["a"]), [["a"], []])
        self.assertEqual(dg.fallbacks([]), [[]])

    def test_request_params(self) -> None:
        s = TranscriptionSettings(engine="deepgram", deepgram_api_key="k")
        params = dg.request_params(s, ["Auril"])
        self.assertIn(("model", "nova-3"), params)
        self.assertIn(("mip_opt_out", "true"), params)  # no training on players' voices
        self.assertIn(("language", "en"), params)
        self.assertIn(("keyterm", "Auril"), params)
        auto = dg.request_params(TranscriptionSettings(engine="deepgram", language=""), [])
        self.assertIn(("detect_language", "true"), auto)
        self.assertNotIn("language", [k for k, _ in auto])

    def test_transcript_parsing(self) -> None:
        self.assertEqual(dg.transcript(reply("  I cast  Detect Magic ")), "I cast Detect Magic")
        bad: list[Any] = [{}, {"results": {"channels": []}}, None, {"results": "x"}]
        for payload in bad:
            self.assertIsNone(dg.transcript(payload))

    def test_factory_builds_it(self) -> None:
        t = build_transcriber(TranscriptionSettings(engine="deepgram", deepgram_api_key="k"))
        self.assertIsInstance(t, DeepgramTranscriber)

    def test_key_not_in_settings_repr(self) -> None:
        s = TranscriptionSettings(engine="deepgram", deepgram_api_key="dg-secret")
        self.assertNotIn("dg-secret", repr(s))
        self.assertTrue(s.sends_audio_out)
        self.assertFalse(TranscriptionSettings().sends_audio_out)  # local Whisper


class DeepgramTranscriberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.received: dict[str, Any] = {}
        self.reply: tuple[int, Any] = (200, reply("Welcome to Bryn Shander."))
        self.replies: list[tuple[int, Any]] = []  # consumed first, if set
        self.hits = 0
        self.max_keyterm_chars: int | None = None  # longer keyterm lists get a 400
        self.sent: list[list[str]] = []  # keyterms of each request, in order
        self.retry_after: str | None = None  # Retry-After on replies from self.replies
        self.hold: asyncio.Event | None = None  # the next request waits for this first
        self.held = asyncio.Event()  # set once that request has arrived
        # A fake clock for Deepgram's waits, moved forward by its sleeps: tests never
        # depend on the real clock, and a wait looked at again sees the time pass.
        self.now = [1000.0]
        clock = patch.object(dg, "_clock", lambda: self.now[0])
        clock.start()
        self.addCleanup(clock.stop)

        async def handler(request: web.Request) -> web.Response:
            self.received["auth"] = request.headers.get("Authorization")
            self.received["type"] = request.headers.get("Content-Type")
            self.received["query"] = list(request.query.items())
            self.received["body"] = await request.read()
            self.hits += 1
            terms = [v for k, v in request.query.items() if k == "keyterm"]
            self.sent.append(terms)
            if self.hold is not None:
                held, self.hold = self.hold, None
                self.held.set()
                await held.wait()  # an answer still on its way while others go on
                status, body = self.reply
                return web.json_response(body, status=status)
            if self.replies:
                status, body = self.replies.pop(0)
                headers = {"Retry-After": self.retry_after} if self.retry_after else None
                return web.json_response(body, status=status, headers=headers)
            if self.max_keyterm_chars is not None and sum(map(len, terms)) > self.max_keyterm_chars:
                return web.json_response({"err_msg": "too many keyterm tokens"}, status=400)
            status, body = self.reply
            return web.json_response(body, status=status)

        app = web.Application()
        app.router.add_post("/v1/listen", handler)
        self.server = TestServer(app)
        await self.server.start_server()
        self.t = DeepgramTranscriber(
            TranscriptionSettings(
                engine="deepgram",
                deepgram_api_key="dg-test",
                deepgram_url=str(self.server.make_url("/v1/listen")),
            )
        )

    async def asyncTearDown(self) -> None:
        await self.t.close()
        await self.server.close()

    async def test_sends_wav_key_and_keyterms(self) -> None:
        text = await self.t.transcribe(clip(), ["Bryn Shander", "Auril"])
        self.assertEqual(text, "Welcome to Bryn Shander.")
        self.assertEqual(self.received["auth"], "Token dg-test")
        self.assertEqual(self.received["type"], "audio/wav")
        self.assertTrue(self.received["body"].startswith(b"RIFF"))
        query = self.received["query"]
        self.assertIn(("keyterm", "Bryn Shander"), query)
        self.assertIn(("keyterm", "Auril"), query)
        self.assertIn(("mip_opt_out", "true"), query)

    async def test_error_status_raises_without_key_or_body(self) -> None:
        self.reply = (401, {"err_msg": "Invalid credentials dg-test"})
        with self.assertRaises(DeepgramError) as ctx:
            await self.t.transcribe(clip(), [])
        self.assertIn("401", str(ctx.exception))
        self.assertIn("didn't accept DEEPGRAM_API_KEY", str(ctx.exception))  # for the log
        self.assertEqual(ctx.exception.for_dm, "Deepgram didn't accept DMbot's key")  # #99
        self.assertTrue(ctx.exception.host_can_fix)  # the DM screen points at .env
        self.assertNotIn("dg-test", str(ctx.exception))
        self.assertNotIn("Invalid", str(ctx.exception))  # provider body is not echoed
        self.assertEqual(self.hits, 1)  # 401 is not retried

    async def test_retries_once_when_busy(self) -> None:
        self.replies = [(429, {"err_msg": "slow down"})]
        with patch("dmbot.transcription.deepgram.RETRY_DELAY_S", 0):
            text = await self.t.transcribe(clip(), [])
        self.assertEqual(text, "Welcome to Bryn Shander.")
        self.assertEqual(self.hits, 2)

    async def busy_once(
        self, retry_after: str | None, status: int = 429
    ) -> tuple[str | None, list[float]]:
        """One busy reply with this Retry-After, then the usual answer. Returns the text
        and the waits DMbot made (without really waiting)."""
        self.replies = [(status, {"err_msg": "slow down"})]
        self.retry_after = retry_after
        return await self.transcribe_counting_waits()

    async def transcribe_counting_waits(self) -> tuple[str | None, list[float]]:
        waits: list[float] = []

        async def no_wait(seconds: float) -> None:
            waits.append(seconds)
            self.now[0] += seconds  # the wait passes

        with patch.object(dg, "_sleep", no_wait):
            return await self.t.transcribe(clip(), []), waits

    async def test_waits_as_long_as_deepgram_asks(self) -> None:
        for status in (429, 503):
            self.hits = 0
            with self.subTest(status):
                text, waits = await self.busy_once("2", status)
                self.assertEqual((text, self.hits), ("Welcome to Bryn Shander.", 2))
                self.assertEqual(waits, [2.0])  # exactly: a fake clock, not the real one
                # Answered: the next clip doesn't wait.
                self.assertEqual(await self.transcribe_counting_waits(), (text, []))

    async def test_usual_pause_without_a_usable_retry_after(self) -> None:
        values = (None, "Wed, 21 Oct 2026 07:28:00 GMT", "nan", "inf", "1e400", "-4")
        for value in values:
            with self.subTest(value):
                _, waits = await self.busy_once(value)
                self.assertEqual(waits, [dg.RETRY_DELAY_S])

    async def test_a_long_wait_fails_the_clip_at_once(self) -> None:
        # One worker writes for every table: waiting 30 s would hold them all up.
        for value in ("30", "3600"):
            self.hits = 0
            self.t._busy_until = 0.0
            with self.subTest(value), self.assertRaises(DeepgramError) as ctx:
                await self.busy_once(value)
            self.assertEqual(self.hits, 1)
            self.assertEqual(ctx.exception.for_dm, dg.BUSY)
            self.assertFalse(ctx.exception.host_can_fix)

    async def test_later_clips_respect_the_wait_without_asking_again(self) -> None:
        with self.assertRaises(DeepgramError):
            await self.busy_once("30")
        self.assertEqual(self.hits, 1)
        with self.assertRaises(DeepgramError) as ctx:
            await self.transcribe_counting_waits()  # well inside the 30 s
        self.assertEqual(self.hits, 1)  # no request sent
        self.assertEqual(ctx.exception.for_dm, dg.BUSY)

    async def test_busy_twice_says_busy(self) -> None:
        self.reply = (429, {"err_msg": "slow down"})
        with (
            patch("dmbot.transcription.deepgram.RETRY_DELAY_S", 0),
            self.assertRaises(DeepgramError) as ctx,
        ):
            await self.t.transcribe(clip(), [])
        self.assertEqual(self.hits, 2)
        self.assertEqual(ctx.exception.for_dm, dg.BUSY)  # the same words either way

    async def test_the_remembered_wait_is_capped_and_expires(self) -> None:
        # An hour from a proxy: this clip fails, and the window is only a minute.
        with self.assertRaises(DeepgramError):
            await self.busy_once("3600")
        self.assertEqual(self.t._busy_until, 1000.0 + dg.MAX_BUSY_WINDOW_S)
        # Inside the window: no request, "busy" at once.
        self.now[0] += 30
        with self.assertRaises(DeepgramError):
            await self.transcribe_counting_waits()
        self.assertEqual(self.hits, 1)
        # 1.5 s left: wait those 1.5 s, not the header's hour, then ask.
        self.now[0] = 1000.0 + dg.MAX_BUSY_WINDOW_S - 1.5
        text, waits = await self.transcribe_counting_waits()
        self.assertEqual((text, waits, self.hits), ("Welcome to Bryn Shander.", [1.5], 2))
        # Answered after the window was set: cleared.
        self.assertEqual(self.t._busy_until, 0.0)

    async def test_a_wait_set_by_another_worker_meanwhile_is_honoured(self) -> None:
        # Waiting out 1 s, another worker's 429 makes it 1.5 s more: wait that too (#470).
        self.t._busy_until, self.t._busy_set_at = 1001.0, 1000.0
        waits: list[float] = []

        async def wait(seconds: float) -> None:
            waits.append(seconds)
            self.now[0] += seconds
            if len(waits) == 1:
                self.t._busy_until, self.t._busy_set_at = self.now[0] + 1.5, self.now[0]

        with patch.object(dg, "_sleep", wait):
            self.assertEqual(await self.t.transcribe(clip(), []), "Welcome to Bryn Shander.")
        self.assertEqual(waits, [1.0, 1.5])

    async def test_an_answer_in_flight_never_clears_a_newer_wait(self) -> None:
        # Two workers (#173, #470): clip A's request is on its way; clip B's gets a 429
        # with Retry-After 30 (a minute's window at most). A's 200 then arrives: it was
        # sent before the window, so it says nothing about now. The window stays.
        release = self.hold = asyncio.Event()
        first = asyncio.create_task(self.t.transcribe(clip(guild_id=1), []))
        await asyncio.wait_for(self.held.wait(), 5)  # A is on its way
        self.now[0] += 0.5
        self.replies = [(429, {"err_msg": "slow down"})]
        self.retry_after = "30"
        with self.assertRaises(DeepgramError):  # 30 s: more than a clip may wait
            await self.t.transcribe(clip(guild_id=2), [])
        window = self.t._busy_until
        self.assertEqual(window, 1000.5 + 30)
        release.set()
        self.assertEqual(await first, "Welcome to Bryn Shander.")
        self.assertEqual(self.t._busy_until, window)  # still waiting it out

    async def test_a_refusal_with_retry_after_sets_no_wait(self) -> None:
        self.reply = (401, {"err_msg": "Invalid credentials"})
        self.retry_after = "30"
        self.replies = [(401, {"err_msg": "Invalid credentials"})]
        with self.assertRaises(DeepgramError):
            await self.t.transcribe(clip(), [])
        self.assertEqual(self.t._busy_until, 0.0)  # only "busy" answers set a wait

    def test_retry_after_parsing(self) -> None:
        self.assertEqual(dg.retry_after_s(" 3 "), 3.0)
        self.assertEqual(dg.retry_after_s("0.5"), 0.5)
        self.assertEqual(dg.retry_after_s("0"), 0.0)
        for broken in ("-4", "inf", "-inf", "1e400", "9" * 400):  # never a window
            self.assertIsNone(dg.retry_after_s(broken), broken)
        self.assertIsNone(dg.retry_after_s(None))
        self.assertIsNone(dg.retry_after_s("nan"))
        self.assertIsNone(dg.retry_after_s("soon"))

    async def test_gives_up_after_second_failure(self) -> None:
        self.reply = (503, {"err_msg": "down"})
        with (
            patch("dmbot.transcription.deepgram.RETRY_DELAY_S", 0),
            self.assertRaises(DeepgramError),
        ):
            await self.t.transcribe(clip(), [])
        self.assertEqual(self.hits, 2)

    async def test_refused_keyterms_are_halved_and_the_length_remembered(self) -> None:
        names = [f"Caer-Dineval-{i:02}" for i in range(30)]  # 15 characters each
        self.max_keyterm_chars = 250  # more than about 16 names is refused
        with self.assertLogs("dmbot.transcription.deepgram", "WARNING") as logs:
            text = await self.t.transcribe(clip(), names)
        self.assertEqual(text, "Welcome to Bryn Shander.")
        self.assertEqual([len(t) for t in self.sent], [30, 15])  # the most relevant half
        self.assertEqual(self.sent[1], names[:15])
        self.assertNotIn("Caer", "\n".join(logs.output))  # counts only, never names
        # The next clip goes straight to a length that works: no refusal first.
        self.sent.clear()
        await self.t.transcribe(clip(), names)
        self.assertEqual([len(t) for t in self.sent], [15])

    async def test_no_keyterms_as_a_last_resort(self) -> None:
        self.max_keyterm_chars = 0  # any keyterm is refused
        with self.assertLogs("dmbot.transcription.deepgram", "WARNING"):
            text = await self.t.transcribe(clip(), ["Auril", "Bryn Shander"])
        self.assertEqual(text, "Welcome to Bryn Shander.")
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"], ["Auril"], []])
        # Hints come back on later clips, a few at least: MIN_KEYTERM_CHARS, exactly.
        self.sent.clear()
        self.max_keyterm_chars = None
        names = [f"Name{i:02}" for i in range(40)]  # 6 characters each
        await self.t.transcribe(clip(), names)
        self.assertEqual(sum(map(len, self.sent[0])), 96)  # 16 names fit in 100
        self.assertEqual(self.sent[0], names[:16])

    async def test_the_learned_length_only_goes_down(self) -> None:
        names = [f"Caer-Dineval-{i:02}" for i in range(30)]  # 15 characters each
        self.max_keyterm_chars = 250
        with self.assertLogs("dmbot.transcription.deepgram", "WARNING"):
            await self.t.transcribe(clip(), names)  # 15 names worked: 225 characters
            self.max_keyterm_chars = 100
            await self.t.transcribe(clip(), names)  # only none worked: down to the floor
        self.max_keyterm_chars = None
        self.sent.clear()
        await self.t.transcribe(clip(), names[:3])  # a short list works at once
        await self.t.transcribe(clip(), names)
        self.assertEqual([len(t) for t in self.sent], [3, 6])  # 100 characters, not 225

    async def test_one_campaigns_refusals_never_limit_another(self) -> None:
        names = [f"Caer-Dineval-{i:02}" for i in range(30)]
        self.max_keyterm_chars = 250
        with self.assertLogs("dmbot.transcription.deepgram", "WARNING"):
            await self.t.transcribe(clip(guild_id=1, session=1), names)
        self.max_keyterm_chars = None
        self.sent.clear()
        await self.t.transcribe(clip(guild_id=2, session=2), names)  # another server
        await self.t.transcribe(clip(guild_id=1, session=3), names)  # the next session
        self.assertEqual([len(t) for t in self.sent], [30, 30])

    async def test_busy_then_refused_still_falls_back(self) -> None:
        self.max_keyterm_chars = 6
        self.replies = [(429, {"err_msg": "slow down"})]
        with (
            patch("dmbot.transcription.deepgram.RETRY_DELAY_S", 0),
            self.assertLogs("dmbot.transcription.deepgram", "WARNING"),
        ):
            text = await self.t.transcribe(clip(), ["Auril", "Bryn Shander"])
        self.assertEqual(text, "Welcome to Bryn Shander.")
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"]] * 2 + [["Auril"]])

    async def test_a_400_without_keyterms_points_at_the_settings(self) -> None:
        self.reply = (400, {"err_msg": "bad model"})
        with (
            self.assertLogs("dmbot.transcription.deepgram", "WARNING") as logs,
            self.assertRaises(DeepgramError) as ctx,
        ):
            await self.t.transcribe(clip(), ["Auril", "Bryn Shander"])
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"], ["Auril"], []])
        self.assertIn("400", str(ctx.exception))
        self.assertTrue(ctx.exception.host_can_fix)  # even without keyterms: settings
        self.assertNotIn("Auril", str(ctx.exception))
        self.assertNotIn("Auril", "\n".join(logs.output))
        # Later clips don't pay for the fallbacks again: one request, then the error.
        self.sent.clear()
        with self.assertRaises(DeepgramError):
            await self.t.transcribe(clip(), ["Auril", "Bryn Shander"])
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"]])
        # One answer shows it wasn't the settings after all: the fallbacks come back.
        self.reply = (200, reply("ok"))
        await self.t.transcribe(clip(), [])
        self.reply = (400, {"err_msg": "too many keyterm tokens"})
        self.replies = [(400, {}), (200, reply("Auril"))]
        self.sent.clear()
        with self.assertLogs("dmbot.transcription.deepgram", "WARNING"):
            self.assertEqual(await self.t.transcribe(clip(), ["Auril", "Bryn Shander"]), "Auril")
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"], ["Auril"]])

    async def test_no_speech_is_none(self) -> None:
        self.reply = (200, reply(""))
        self.assertIsNone(await self.t.transcribe(clip(), []))

    async def test_unreadable_reply_is_a_plain_failure(self) -> None:
        async def garbage(request: web.Request) -> web.Response:
            return web.Response(text="not json", content_type="text/plain")

        app = web.Application()
        app.router.add_post("/v1/listen", garbage)
        server = TestServer(app)
        await server.start_server()
        t = DeepgramTranscriber(
            TranscriptionSettings(
                engine="deepgram",
                deepgram_api_key="dg-test",
                deepgram_url=str(server.make_url("/v1/listen")),
            )
        )
        try:
            with self.assertRaisesRegex(DeepgramError, "couldn't read"):
                await t.transcribe(clip(), ["Auril"])
        finally:
            await t.close()
            await server.close()

    async def test_a_timeout_isnt_retried(self) -> None:
        async def slow(request: web.Request) -> web.Response:
            await asyncio.sleep(2)
            return web.json_response(reply("late"))

        self.server.app.router._frozen = False  # add a route for this test
        self.server.app.router.add_post("/slow", slow)
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.2))
        t = DeepgramTranscriber(
            TranscriptionSettings(
                engine="deepgram",
                deepgram_api_key="dg-test",
                deepgram_url=str(self.server.make_url("/slow")),
            ),
            session,
        )
        try:
            with self.assertRaisesRegex(DeepgramError, "took too long"):
                await t.transcribe(clip(), [])
        finally:
            await session.close()
        self.assertFalse(DeepgramError("x").host_can_fix)  # an outage, not .env

    async def test_unreachable_is_retried_once_then_names_stay_out_of_the_error(self) -> None:
        dead = DeepgramTranscriber(
            TranscriptionSettings(
                engine="deepgram",
                deepgram_api_key="dg-test",
                deepgram_url="http://127.0.0.1:9/v1/listen",  # nothing listens here
            )
        )
        try:
            with self.assertRaises(DeepgramError) as ctx:
                await dead.transcribe(clip(), ["Bryn Shander"])
        finally:
            await dead.close()
        message = str(ctx.exception)
        self.assertIn("couldn't reach Deepgram", message)
        for secret in ("dg-test", "Bryn", "keyterm", "127.0.0.1"):
            self.assertNotIn(secret, message)

    async def test_close_twice_is_safe(self) -> None:
        await self.t.close()
        await self.t.close()
