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


def clip() -> Utterance:
    return Utterance(1, 2, 0, 0, bytes(3200))


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

        async def handler(request: web.Request) -> web.Response:
            self.received["auth"] = request.headers.get("Authorization")
            self.received["type"] = request.headers.get("Content-Type")
            self.received["query"] = list(request.query.items())
            self.received["body"] = await request.read()
            self.hits += 1
            terms = [v for k, v in request.query.items() if k == "keyterm"]
            self.sent.append(terms)
            if self.max_keyterm_chars is not None and sum(map(len, terms)) > self.max_keyterm_chars:
                return web.json_response({"err_msg": "too many keyterm tokens"}, status=400)
            status, body = self.replies.pop(0) if self.replies else self.reply
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
        # Hints come back on later clips, a few at least (MIN_KEYTERM_CHARS).
        self.sent.clear()
        self.max_keyterm_chars = None
        await self.t.transcribe(clip(), ["Auril", "Bryn Shander"])
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"]])

    async def test_a_400_without_keyterms_points_at_the_settings(self) -> None:
        self.reply = (400, {"err_msg": "bad model"})
        with (
            self.assertLogs("dmbot.transcription.deepgram", "WARNING"),
            self.assertRaises(DeepgramError) as ctx,
        ):
            await self.t.transcribe(clip(), ["Auril", "Bryn Shander"])
        self.assertEqual(self.sent, [["Auril", "Bryn Shander"], ["Auril"], []])
        self.assertIn("400", str(ctx.exception))
        self.assertTrue(ctx.exception.host_can_fix)  # even without keyterms: settings
        self.assertNotIn("Auril", str(ctx.exception))

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
