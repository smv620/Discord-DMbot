import asyncio
import importlib.util
import json
import unittest

HAS_WEBSOCKETS = importlib.util.find_spec("websockets") is not None


@unittest.skipUnless(HAS_WEBSOCKETS, "websockets not installed")
class EarsServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from dmbot.ears.protocol import AudioFrame, EarsMessage
        from dmbot.ears.server import EarsServer

        self.messages: list[EarsMessage] = []
        self.frames: list[AudioFrame] = []
        self.links: list[bool] = []

        async def on_message(m: EarsMessage) -> None:
            self.messages.append(m)

        async def on_link(up: bool) -> None:
            self.links.append(up)

        self.server = EarsServer("127.0.0.1", 0, "secret", on_message, self.frames.append, on_link)
        await self.server.start()
        self.url = f"ws://127.0.0.1:{self.server.port}"

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_rejects_bad_secret(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        async with connect(self.url) as ws:
            await ws.send(json.dumps({"type": "hello", "version": 1, "secret": "wrong"}))
            with self.assertRaises(ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 2)
        self.assertFalse(self.server.connected)
        self.assertEqual(self.links, [])

    async def test_accepts_and_routes(self) -> None:
        from websockets.asyncio.client import connect

        from dmbot.ears.protocol import Status, encode_audio_frame, leave_command

        async with connect(self.url) as ws:
            await ws.send(json.dumps({"type": "hello", "version": 1, "secret": "secret"}))
            await ws.send(json.dumps({"type": "status", "state": "ready"}))
            await ws.send(encode_audio_frame(1, 2, 3, bytes(640)))
            await ws.send(b"\x09garbage")
            for _ in range(50):
                if self.server.connected and self.frames and self.messages:
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(self.server.connected)
            self.assertEqual(self.messages, [Status("ready")])
            self.assertEqual(self.frames[0].user_id, 2)
            self.assertTrue(await self.server.send(leave_command(1)))
            self.assertEqual(json.loads(await ws.recv())["type"], "leave")
        for _ in range(50):
            if not self.server.connected:
                break
            await asyncio.sleep(0.02)
        self.assertFalse(self.server.connected)
        self.assertEqual(self.links, [True, False])
        self.assertEqual(self.server.rejected_frames, 1)
