import unittest
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.cloud import CloudTranscriber, CloudTranscriptionError
from dmbot.transcription.config import TranscriptionSettings


class CloudTranscriberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.received: dict[str, Any] = {}
        self.reply: tuple[int, Any] = (200, {"text": "  I cast  Hold Person "})

        async def handler(request: web.Request) -> web.Response:
            self.received["auth"] = request.headers.get("Authorization")
            form = await request.post()
            self.received["fields"] = {k: v for k, v in form.items() if k != "file"}
            upload = form["file"]
            assert isinstance(upload, web.FileField)
            self.received["file"] = upload.file.read()
            status, body = self.reply
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

    async def test_missing_text_is_none(self) -> None:
        self.reply = (200, {"unexpected": True})
        self.assertIsNone(await self.t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), []))
