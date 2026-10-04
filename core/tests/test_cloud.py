import unittest
from typing import Any
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.cloud import CloudTranscriber, CloudTranscriptionError
from dmbot.transcription.config import TranscriptionSettings


class CloudTranscriberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.received: dict[str, Any] = {}
        self.reply: tuple[int, Any] = (200, {"text": "  I cast  Hold Person "})
        self.replies: list[tuple[int, Any]] = []  # consumed first, if set
        self.hits = 0

        async def handler(request: web.Request) -> web.Response:
            self.received["auth"] = request.headers.get("Authorization")
            form = await request.post()
            self.received["fields"] = {k: v for k, v in form.items() if k != "file"}
            upload = form["file"]
            assert isinstance(upload, web.FileField)
            self.received["file"] = upload.file.read()
            self.hits += 1
            status, body = self.replies.pop(0) if self.replies else self.reply
            return web.json_response(body, status=status)

        app = web.Application()
        app.router.add_post("/v1/audio/transcriptions", handler)
        self.server = TestServer(app)
        await self.server.start_server()
        url = str(self.server.make_url("/v1/audio/transcriptions"))
        self.t = CloudTranscriber(
            TranscriptionSettings(
                engine="cloud", cloud_url=url, cloud_api_key="sk-test", cloud_model="whisper-1"
            )
        )

    async def asyncTearDown(self) -> None:
        await self.t.close()
        await self.server.close()

    async def test_sends_wav_and_fields(self) -> None:
        text = await self.t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), ["Strahd"])
        self.assertEqual(text, "I cast Hold Person")
        self.assertEqual(self.received["auth"], "Bearer sk-test")
        self.assertEqual(
            self.received["fields"],
            {
                "model": "whisper-1",
                "response_format": "json",
                "language": "en",
                "prompt": "Names: Strahd.",
            },
        )
        self.assertTrue(self.received["file"].startswith(b"RIFF"))

    async def test_error_status_raises_without_key(self) -> None:
        self.reply = (401, {"error": "bad key"})
        with self.assertRaises(CloudTranscriptionError) as ctx:
            await self.t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), [])
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("sk-test", str(ctx.exception))
        self.assertNotIn("bad key", str(ctx.exception))  # provider body is not echoed
        self.assertEqual(self.hits, 1)  # 401 is not retried

    async def test_retries_once_on_server_error(self) -> None:
        self.replies = [(503, {"error": "busy"})]
        with patch("dmbot.transcription.cloud.RETRY_DELAY_S", 0):
            text = await self.t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), [])
        self.assertEqual(text, "I cast Hold Person")
        self.assertEqual(self.hits, 2)

    async def test_gives_up_after_second_failure(self) -> None:
        self.reply = (429, {"error": "slow down"})
        with (
            patch("dmbot.transcription.cloud.RETRY_DELAY_S", 0),
            self.assertRaises(CloudTranscriptionError),
        ):
            await self.t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), [])
        self.assertEqual(self.hits, 2)

    async def test_close_twice_is_safe(self) -> None:
        await self.t.close()
        await self.t.close()

    async def test_missing_text_is_none(self) -> None:
        self.reply = (200, {"unexpected": True})
        self.assertIsNone(await self.t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), []))
