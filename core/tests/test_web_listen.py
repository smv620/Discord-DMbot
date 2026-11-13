"""🎤 Say it (#2316): the website queues a clip, the bot turns it into a respelling.

Run as the website's own database role: only a DM of the campaign can send or read a clip, a
secret name or another campaign's name is "not found", the switch and the plan say `voice_off`,
the door holds a campaign to 6 clips a minute and 3 waiting, and the clip is never returned. The
bot's half runs with a stand-in recogniser: the sender and plan are checked again, the clip is
cleared in the same write as the answer or the failure, a stalled clip is worked at most twice,
and nothing the DM said reaches a log line.
"""

from __future__ import annotations

import io
import logging
import math
import struct
import time
import wave
from types import SimpleNamespace
from typing import Any

import httpx

from dmbot import entitlements
from dmbot.dm_screen import said_as_listens as bot
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.said_as.recogniser import FAILED, VOICE_OFF, RecogniserError
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.listen import queue_listen, read_listen, wait_for_answer
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings

IPA = "kɑˈzɛθ"


def tone(seconds: float) -> bytes:
    """A short wav file of a steady tone: a real clip the decoder can read."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(16000 * seconds))
            )
        )
    return out.getvalue()


class FakeRecogniser:
    def __init__(self, ipa: str = IPA, error: str = "") -> None:
        self.ipa, self.error, self.heard = ipa, error, 0

    async def recognise(self, pcm: bytes) -> str:
        self.heard += 1
        if self.error:
            raise RecogniserError(self.error)
        return self.ipa

    async def unload_if_idle(self) -> None:
        return None


class FakeHost:
    def __init__(self, campaign: Any, recogniser: FakeRecogniser | None, plan: bool = True) -> None:
        self.campaign, self.plan, self.listener = campaign, plan, recogniser

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Any:
        return self.campaign

    async def plan_allows_narration(self, guild_id: int, campaign: Any) -> bool:
        return self.plan

    def recogniser(self) -> FakeRecogniser | None:
        return self.listener


class Listening(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        g = THURSDAY.id
        kazeth = await self.memory.add_entity(
            g, self.one.id, type="npc", name="Ka'zeth", source=DM_SOURCE, status="confirmed"
        )
        self.secret = (
            await self.memory.add_alias(
                g, self.one.id, kazeth.value.id, "the Grey Voice", kind="title",
                source=DM_SOURCE, status="confirmed", secret=True,
            )
        ).value.id  # fmt: skip
        await self.memory.add_entity(
            g, self.two.id, type="npc", name="Brynwater", source=DM_SOURCE, status="confirmed"
        )
        self.cref = f"{g}.{self.one.id}"
        self.main = await self.alias_of("Ka'zeth", self.one.id)
        self.theirs = await self.alias_of("Brynwater", self.two.id)
        token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)  # type: ignore[arg-type]
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        self.bob = found
        self.clip = tone(1.0)

    async def alias_of(self, text: str, campaign: str) -> str:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_aliases WHERE campaign_id = %s AND text = %s",
                (campaign, text),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def send(
        self, clip: bytes | None = None, kind: str | None = "audio/wav", **kw: Any
    ) -> str:
        who = kw.pop("who", self.session)
        return await queue_listen(
            self.web, who, self.cref, kw.pop("alias", self.main),
            self.clip if clip is None else clip, kind,
            voice_on=kw.pop("voice_on", True), now=NOW, enforce_plans=kw.pop("enforce", False),
        )  # fmt: skip

    async def rows(self) -> list[Any]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT id, state, container, clip IS NULL AS cleared, respelling, ipa, error"
                " FROM said_as_listens ORDER BY created_at"
            )
            return list(await cur.fetchall())

    async def refused(self, code: str, **kw: Any) -> None:
        with self.assertRaises(RecapError) as caught:
            await self.send(**kw)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(await self.rows(), [])

    # ---- the website's half -------------------------------------------------------------

    async def test_a_dm_queues_a_clip_and_it_is_never_given_back(self) -> None:
        heard = await self.send(kind="audio/webm;codecs=opus")
        self.assertEqual([(r["id"], r["state"], r["container"]) for r in await self.rows()],
                         [(heard, "waiting", "webm")])  # fmt: skip
        seen = await read_listen(self.web, self.session, self.cref, heard)
        self.assertEqual((seen["state"], seen["respelling"]), ("waiting", ""))
        self.assertNotIn("clip", str(seen))

    async def test_a_stranger_a_secret_name_and_another_campaigns_name_are_not_found(self) -> None:
        await self.refused("not_found", who=self.bob)
        await self.refused("not_found", alias=self.secret)
        await self.refused("not_found", alias=self.theirs)
        await self.refused("not_found", alias="not-an-id")

    async def test_the_switch_the_plan_and_the_clip_itself_are_checked(self) -> None:
        await self.refused("voice_off", voice_on=False)
        await self.refused("voice_off", enforce=True)  # the owner has no plan with narration
        await self.refused("bad_clip", kind="video/mp4")
        await self.refused("bad_clip", kind=None)
        await self.refused("bad_clip", clip=b"")
        await self.refused("too_big", clip=b"\x00" * 1_000_001)

    async def test_an_owner_with_narration_may_send(self) -> None:
        entitlements.configure_free_users({ALICE.id})
        self.addCleanup(entitlements.configure_free_users, ())
        heard = await self.send(enforce=True)
        self.assertEqual([r["id"] for r in await self.rows()], [heard])

    async def test_three_waiting_and_six_a_minute_are_the_most(self) -> None:
        for _ in range(3):
            await self.send()
        with self.assertRaises(RecapError) as caught:
            await self.send()
        self.assertEqual(caught.exception.code, "too_soon")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE said_as_listens SET state = 'done', clip = NULL")
        for _ in range(3):
            await self.send()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE said_as_listens SET state = 'done', clip = NULL")
        with self.assertRaises(RecapError) as caught:
            await self.send()
        self.assertEqual(caught.exception.code, "too_soon")

    async def test_only_the_sender_reads_a_clip(self) -> None:
        heard = await self.send()
        for who, ident in ((self.bob, heard), (self.session, "0" * 32), (self.session, "bad")):
            with self.assertRaises(RecapError):
                await read_listen(self.web, who, self.cref, ident)

    async def test_the_waiting_gives_up_after_the_time_and_returns_a_finished_answer(self) -> None:
        calls = {"n": 0}

        async def read() -> dict[str, Any]:
            calls["n"] += 1
            return {"state": "done" if calls["n"] == 3 else "working"}

        got = await wait_for_answer(read, wait_s=5, poll_s=0.01)
        assert got is not None
        self.assertEqual((got["state"], calls["n"]), ("done", 3))

        async def never() -> dict[str, Any]:
            return {"state": "waiting"}

        self.assertIsNone(await wait_for_answer(never, wait_s=0.05, poll_s=0.01))

    # ---- the bot's half -----------------------------------------------------------------

    def campaign(self, *dms: int) -> Any:
        return SimpleNamespace(
            dm_user_ids=frozenset(dms or (ALICE.id,)), dm_accounts=frozenset(dms or (ALICE.id,))
        )

    def item(self, clip: bytes | None = None, **kw: Any) -> bot.Listen:
        return bot.Listen(
            "a" * 32, self.one.id, kw.get("user", ALICE.id), kw.get("container", "wav"),
            self.clip if clip is None else clip, 1, kw.get("tries", 1),
        )  # fmt: skip

    async def test_a_clip_becomes_a_respelling(self) -> None:
        listener = FakeRecogniser()
        done = await bot.apply(FakeHost(self.campaign(), listener), THURSDAY.id, self.item())
        self.assertEqual((done.error, done.respelling, done.ipa), ("", "kah-ZETH", IPA))
        self.assertEqual(listener.heard, 1)

    async def test_the_sender_the_plan_and_the_switch_are_checked_again(self) -> None:
        listener = FakeRecogniser()
        cases = (
            (FakeHost(self.campaign(BOB.id), listener), bot.NOT_ALLOWED),
            (FakeHost(None, listener), bot.NOT_FOUND),
            (FakeHost(self.campaign(), listener, plan=False), VOICE_OFF),
            (FakeHost(self.campaign(), None), VOICE_OFF),
        )
        for host, word in cases:
            with self.subTest(word=word):
                done = await bot.apply(host, THURSDAY.id, self.item())
                self.assertEqual(done.error, word)
        self.assertEqual(listener.heard, 0)  # nothing was listened to

    async def test_clips_that_cannot_be_used_are_bad_clips(self) -> None:
        host = FakeHost(self.campaign(), FakeRecogniser())
        for label, item in (
            ("too long", self.item(tone(10.5))),
            ("too short", self.item(tone(0.1))),
            ("not audio", self.item(b"not audio at all")),
            ("wrong container", self.item(container="mp3")),
        ):
            with self.subTest(label):
                done = await bot.apply(host, THURSDAY.id, item)
                self.assertEqual(done.error, bot.BAD_CLIP)

    async def test_no_vowel_heard_and_a_recogniser_failure_are_words(self) -> None:
        silent = await bot.apply(
            FakeHost(self.campaign(), FakeRecogniser("stɹ")), THURSDAY.id, self.item()
        )
        self.assertEqual(silent.error, bot.BAD_CLIP)
        broken = await bot.apply(
            FakeHost(self.campaign(), FakeRecogniser(error=FAILED)), THURSDAY.id, self.item()
        )
        self.assertEqual(broken.error, FAILED)

    async def test_a_stalled_clip_is_worked_at_most_twice(self) -> None:
        listener = FakeRecogniser()
        done = await bot.apply(
            FakeHost(self.campaign(), listener), THURSDAY.id, self.item(tries=bot.TRIES_MAX + 1)
        )
        self.assertEqual((done.error, listener.heard), (FAILED, 0))

    async def test_the_clip_is_cleared_in_the_write_that_stores_the_answer(self) -> None:
        store = bot.ListenStore(self.db)
        for listener, expected in (
            (FakeRecogniser(), "done"),
            (FakeRecogniser(error=FAILED), "failed"),
        ):
            heard = await self.send()
            item = await store.claim_next(THURSDAY.id)
            assert item is not None and item.id == heard and item.clip == self.clip
            self.assertIsNone(await store.claim_next(THURSDAY.id))
            host = FakeHost(self.campaign(), listener)
            self.assertTrue(await bot.run_one(host, store, THURSDAY.id, item))
            row = (await self.rows())[-1]
            self.assertEqual((row["state"], row["cleared"]), (expected, True))
            seen = await read_listen(self.web, self.session, self.cref, heard)
            self.assertEqual(seen["state"], expected)
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute("DELETE FROM said_as_listens")

    async def test_a_late_worker_cannot_overwrite_and_old_rows_are_swept(self) -> None:
        clock = [float(NOW)]
        store = bot.ListenStore(self.db, clock=lambda: clock[0])
        await self.send()
        first = await store.claim_next(THURSDAY.id)
        assert first is not None
        clock[0] += bot.RETAKE_AFTER_S + 5
        second = await store.claim_next(THURSDAY.id)
        assert second is not None
        self.assertEqual((first.tries, second.tries), (1, 2))
        self.assertFalse(await store.finish(THURSDAY.id, first, bot.Outcome(respelling="late")))
        self.assertTrue(await store.finish(THURSDAY.id, second, bot.Outcome(respelling="ok")))
        # (the door made the row at this test's real time, not at NOW, which is when the file was
        # loaded: late in a long run the row is younger than the sweep's age and none is swept)
        later = int(time.time()) + bot.KEEP_FOR_S + 1000
        old = bot.ListenStore(self.db, clock=lambda: later)
        self.assertEqual(await old.sweep(THURSDAY.id), 1)

    async def test_nothing_the_dm_said_reaches_a_log_line(self) -> None:
        store = bot.ListenStore(self.db)
        await self.send()
        item = await store.claim_next(THURSDAY.id)
        assert item is not None
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("dmbot").info("start")
            await bot.run_one(
                FakeHost(self.campaign(), FakeRecogniser("zɔɹblæks")), store, THURSDAY.id, item
            )
        text = "\n".join(logs.output)
        self.assertNotIn("zɔɹblæks", text)
        self.assertNotIn("ZAWR", text.upper().replace("ZAWR-", "ZAWR"))  # (no respelling either)

    async def test_the_doors_answer_only_a_dm_of_that_campaign(self) -> None:
        async with self.web.user(BOB.id, session=self.bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_listen_add(%s, %s, 'wav', 'x'::bytea) AS a,"
                " dmbot_web_listen_read(%s, %s, %s) AS r",
                (THURSDAY.id, self.one.id, THURSDAY.id, self.one.id, "0" * 32),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["a"], row["r"]), (None, None))
        self.assertEqual(await self.rows(), [])


class OverHttp(Listening):
    """The route, signed in as the DM, with a bot that answers while the request waits."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = self.make_client(settings(said_as_voice_on=True), token)

    def make_client(self, config: Any, token: str) -> httpx.AsyncClient:
        app = create_app(config, self.web, FakeDiscord(), clock=lambda: NOW)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        )
        self.addAsyncCleanup(client.aclose)
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        return client

    @property
    def url(self) -> str:
        return f"/me/campaigns/{self.cref}/names/{self.main}/said-as/from-voice"

    async def test_a_clip_is_answered_while_the_page_waits(self) -> None:
        import asyncio

        store = bot.ListenStore(self.db)
        host = FakeHost(self.campaign(), FakeRecogniser())

        async def the_bot() -> None:
            for _ in range(200):
                item = await store.claim_next(THURSDAY.id)
                if item is not None:
                    await bot.run_one(host, store, THURSDAY.id, item)
                    return
                await asyncio.sleep(0.05)

        working = asyncio.create_task(the_bot())
        sent = await self.client.post(
            self.url, content=self.clip, headers={**HEADERS, "Content-Type": "audio/wav"}
        )
        await working
        self.assertEqual(
            (sent.status_code, sent.json()), (200, {"respelling": "kah-ZETH", "ipa": IPA})
        )
        self.assertTrue((await self.rows())[0]["cleared"])

    async def test_refusals_over_http(self) -> None:
        wav = {**HEADERS, "Content-Type": "audio/wav"}
        off = self.make_client(
            settings(), await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        )
        for client, content, headers, status, word in (
            (off, self.clip, wav, 409, "voice_off"),
            (self.client, self.clip, {**HEADERS, "Content-Type": "video/mp4"}, 422, "bad_clip"),
            (self.client, b"", wav, 422, "bad_clip"),
            (self.client, b"\x00" * 1_000_001, wav, 413, "too_big"),
        ):
            with self.subTest(word=word):
                got = await client.post(self.url, content=content, headers=headers)
                self.assertEqual((got.status_code, got.json()), (status, {"error": word}))
        secret = f"/me/campaigns/{self.cref}/names/{self.secret}/said-as/from-voice"
        got = await self.client.post(secret, content=self.clip, headers=wav)
        self.assertEqual(got.status_code, 404)
        self.assertEqual(await self.rows(), [])
