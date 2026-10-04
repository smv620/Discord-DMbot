import asyncio
import importlib.util
import json
import unittest

HAS_WEBSOCKETS = importlib.util.find_spec("websockets") is not None


def hello(**changes: object) -> dict[str, object]:
    msg: dict[str, object] = {
        "type": "hello",
        "version": 2,
        "secret": "secret",
        "shardCount": 4,
        "shardIds": [1, 3],
    }
    msg.update(changes)
    return msg


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

        from dmbot.sharding import ShardSettings

        self.server = EarsServer(
            "127.0.0.1",
            0,
            "secret",
            on_message,
            self.frames.append,
            on_link,
            shards=ShardSettings(4, (1, 3)),
        )
        await self.server.start()
        self.url = f"ws://127.0.0.1:{self.server.port}"

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_rejects_bad_secret(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        async with connect(self.url) as ws:
            await ws.send(json.dumps(hello(secret="wrong")))
            with self.assertRaises(ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 2)
        self.assertFalse(self.server.connected)
        self.assertEqual(self.links, [])

    async def test_accepts_and_routes(self) -> None:
        from websockets.asyncio.client import connect

        from dmbot.ears.protocol import Status, encode_audio_frame, leave_command

        async with connect(self.url) as ws:
            await ws.send(json.dumps(hello()))
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

    async def assert_rejected(self, message: dict[str, object], reason: str) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        with self.assertLogs("dmbot.ears.server", "WARNING") as logs:
            async with connect(self.url) as ws:
                await ws.send(json.dumps(message))
                with self.assertRaises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), 2)
        self.assertIn(reason, "\n".join(logs.output))
        self.assertFalse(self.server.connected)

    async def test_rejects_other_shards(self) -> None:
        await self.assert_rejected(hello(shardIds=[0]), "same SHARD_COUNT and SHARD_IDS")
        await self.assert_rejected(hello(shardCount=8), "same SHARD_COUNT and SHARD_IDS")

    async def test_rejects_old_ears_with_a_clear_reason(self) -> None:
        old = {"type": "hello", "version": 1, "secret": "secret"}
        await self.assert_rejected(old, "update both to the same DMbot version")

    async def test_wrong_secret_is_named_in_the_log(self) -> None:
        await self.assert_rejected(hello(secret="nope"), "wrong EARS_SHARED_SECRET")
