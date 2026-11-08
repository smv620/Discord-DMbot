"""🔊 Hear it (#3807): the website queues a press and the bot answers it in the narrator's voice.

Run as the website's own database role: only a DM of the campaign can press or read, a secret name
or another campaign's name is "not found", the words are held to the suggestion's plain-letters
rule, the voice and plan gates say `voice_off`, the door holds a campaign to 20 presses a minute
and the clip is handed over once. The bot's half runs against a fake voice company: a second
identical press costs one call, the sender and the plan are checked again, and the words never
reach a log line.
"""

from __future__ import annotations

import base64
import calendar
import io
import json
import logging
import wave
from types import SimpleNamespace
from typing import Any

import httpx

from dmbot import entitlements
from dmbot.dm_screen import said_as_hears as bot
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.recaps.audio.eleven import SPEECH_RATE, ElevenLabs
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.hear import queue_hear, read_hear
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings

WORDS = "Kah-zeth"


class FakeVoice:
    """The voice company's transport: counts calls, answers one second of silence."""

    def __init__(self, status: int = 200) -> None:
        self.status, self.calls = status, 0

    async def __call__(self, method: str, path: str, body: Any) -> tuple[int, bytes]:
        self.calls += 1
        if self.status != 200:
            return self.status, b""
        pcm = b"\x00\x00" * SPEECH_RATE
        n = len(body["text"])
        align = {
            "character_start_times_seconds": [0.0] * n,
            "character_end_times_seconds": [1.0] * n,
        }
        data = {"audio_base64": base64.b64encode(pcm).decode(), "alignment": align}
        return 200, json.dumps(data).encode()


class FakeHost:
    def __init__(
        self, campaign: Any, voice: FakeVoice | None, *, plan: bool = True, narrator: bool = True
    ) -> None:
        self.campaign, self.plan, self.on = campaign, plan, narrator
        self.voice = voice or FakeVoice()
        self.used: dict[str, int] = {}  # this month's credits by campaign

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Any:
        return self.campaign

    async def plan_allows_narration(self, guild_id: int, campaign: Any) -> bool:
        return self.plan

    hear_limit = 5000

    async def hear_used(self, guild_id: int, campaign_id: str) -> int:
        return self.used.get(campaign_id, 0)

    async def hear_spent(self, guild_id: int, campaign_id: str, credits: int) -> None:
        self.used[campaign_id] = self.used.get(campaign_id, 0) + credits

    def narrator(self) -> ElevenLabs | None:
        if not self.on:
            return None
        return ElevenLabs(self.voice, voice_id="voice123", speech_model="eleven_v3")


class Hearing(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        g = THURSDAY.id
        add = self.memory.add_entity
        kazeth = await add(g, self.one.id, type="npc", name="Ka'zeth", source=DM_SOURCE,
                           status="confirmed")  # fmt: skip
        self.secret = (
            await self.memory.add_alias(
                g, self.one.id, kazeth.value.id, "the Grey Voice", kind="title",
                source=DM_SOURCE, status="confirmed", secret=True,
            )
        ).value.id  # fmt: skip
        other = await add(g, self.two.id, type="npc", name="Brynwater", source=DM_SOURCE,
                          status="confirmed")  # fmt: skip
        self.cref = f"{g}.{self.one.id}"
        self.main = await self.alias_of("Ka'zeth", self.one.id)
        self.theirs = await self.alias_of("Brynwater", self.two.id)
        assert other
        bob_token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)  # type: ignore[arg-type]
        found = await sessions.find(self.web, bob_token, now=NOW)
        assert found is not None
        self.bob = found

    async def alias_of(self, text: str, campaign: str) -> str:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_aliases WHERE campaign_id = %s AND text = %s",
                (campaign, text),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def press(self, text: object = WORDS, *, who: Any = None, alias: str | None = None,
                    audio_on: bool = True, enforce: bool = False) -> str:  # fmt: skip
        return await queue_hear(
            self.web, who or self.session, self.cref, alias or self.main, text,
            audio_on=audio_on, now=NOW, enforce_plans=enforce,
        )  # fmt: skip

    async def rows(self) -> list[Any]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT id, text, state FROM said_as_hears ORDER BY created_at"
            )
            return list(await cur.fetchall())

    async def refused(self, code: str, **kw: Any) -> None:
        text = kw.pop("text", WORDS)
        with self.assertRaises(RecapError) as caught:
            await self.press(text, **kw)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(await self.rows(), [])  # nothing was queued

    # ---- the website's half -------------------------------------------------------------

    async def test_a_dm_queues_a_press_for_a_listed_name(self) -> None:
        press = await self.press()
        self.assertEqual([(r["id"], r["text"], r["state"]) for r in await self.rows()],
                         [(press, WORDS, "waiting")])  # fmt: skip
        seen = await read_hear(self.web, self.session, self.cref, press)
        self.assertEqual((seen["state"], seen["progress"]["stage"]), ("waiting", "waiting"))
        self.assertNotIn("audio", seen)

    async def test_a_stranger_a_secret_name_and_another_campaigns_name_are_not_found(self) -> None:
        await self.refused("not_found", who=self.bob)  # not a DM of it
        await self.refused("not_found", alias=self.secret)
        await self.refused("not_found", alias=self.theirs)
        await self.refused("not_found", alias="0" * 32)
        await self.refused("not_found", alias="not-an-id")

    async def test_odd_words_are_refused(self) -> None:
        for bad in ("", "  ", "x" * 81, "Kah zeth 2", "bell\x07", "Kah—zeth", "<b>", 5, None):
            with self.subTest(bad=str(bad)[:8]):
                await self.refused("bad_text", text=bad)
        self.assertEqual(
            await self.press("  Ka'zeth  of   the Vale "), (await self.rows())[0]["id"]
        )
        self.assertEqual((await self.rows())[0]["text"], "Ka'zeth of the Vale")

    async def test_no_voice_set_up_or_no_narration_in_the_plan_is_voice_off(self) -> None:
        await self.refused("voice_off", audio_on=False)
        # Plans enforced and the owner (Alice) with none: the same word, nothing queued.
        await self.refused("voice_off", enforce=True)

    async def test_an_owner_with_narration_in_the_plan_may_press(self) -> None:
        entitlements.configure_free_users({ALICE.id})  # the owner, on the free list
        self.addCleanup(entitlements.configure_free_users, ())
        press = await self.press(enforce=True)
        self.assertEqual([r["id"] for r in await self.rows()], [press])

    async def test_twenty_presses_a_minute_for_a_campaign(self) -> None:
        for _ in range(10):
            await self.press()
        async with self.db.guild(THURSDAY.id) as conn:  # answered, so only the minute counts
            await conn.execute("UPDATE said_as_hears SET state = 'done'")
        for _ in range(10):
            await self.press()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE said_as_hears SET state = 'done'")
        with self.assertRaises(RecapError) as caught:
            await self.press()
        self.assertEqual(caught.exception.code, "too_soon")

    async def test_only_the_presser_reads_and_the_clip_is_handed_over_once(self) -> None:
        press = await self.press()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE said_as_hears SET state = 'done', audio = %s", (b"RIFFclip",)
            )
        with self.assertRaises(RecapError):
            await read_hear(self.web, self.bob, self.cref, press)  # another DM's, not Bob's
        first = await read_hear(self.web, self.session, self.cref, press)
        self.assertEqual(base64.b64decode(first["audio"]), b"RIFFclip")
        self.assertEqual(first["mime"], "audio/wav")
        again = await read_hear(self.web, self.session, self.cref, press)
        self.assertEqual(again["state"], "done")
        self.assertNotIn("audio", again)  # cleared as it was handed over
        for gone in ("0" * 32, "bad"):
            with self.assertRaises(RecapError):
                await read_hear(self.web, self.session, self.cref, gone)

    # ---- the bot's half -----------------------------------------------------------------

    async def run_bot(self, host: FakeHost, cache: bot.HearCache | None = None) -> bot.Outcome:
        item = bot.Hear("a" * 32, self.one.id, ALICE.id, WORDS, 1)
        return await bot.apply(host, cache or bot.HearCache(), THURSDAY.id, item)

    def campaign(self, *dms: int) -> Any:
        return SimpleNamespace(dm_user_ids=frozenset(dms or (ALICE.id,)))

    async def test_a_clip_is_made_as_wav_and_the_second_press_is_free(self) -> None:
        voice = FakeVoice()
        host, cache = FakeHost(self.campaign(), voice), bot.HearCache()
        first = await self.run_bot(host, cache)
        assert first.audio is not None
        with wave.open(io.BytesIO(first.audio)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()),
                             (1, 2, SPEECH_RATE))  # fmt: skip
        self.assertEqual((voice.calls, first.credits), (1, len(WORDS)))
        second = await self.run_bot(host, cache)
        self.assertEqual((voice.calls, second.audio, second.credits), (1, first.audio, 0))

    async def error_of(self, host: FakeHost) -> str:
        return (await self.run_bot(host)).error

    async def test_the_sender_and_the_plan_are_checked_again(self) -> None:
        voice, mine = FakeVoice(), self.campaign()
        self.assertEqual(
            await self.error_of(FakeHost(self.campaign(BOB.id), voice)), bot.NOT_ALLOWED
        )
        self.assertEqual(await self.error_of(FakeHost(None, voice)), bot.NOT_FOUND)
        self.assertEqual(await self.error_of(FakeHost(mine, voice, plan=False)), bot.VOICE_OFF)
        self.assertEqual(await self.error_of(FakeHost(mine, voice, narrator=False)), bot.VOICE_OFF)
        self.assertEqual(voice.calls, 0)  # nothing paid for

    async def test_a_voice_company_failure_is_a_word_and_is_not_cached(self) -> None:
        cache, mine = bot.HearCache(), self.campaign()
        failures = ((500, bot.BUSY), (402, bot.BUSY), (401, bot.VOICE_OFF), (400, bot.FAILED))
        for status, word in failures:
            with self.subTest(status=status):
                done = await self.run_bot(FakeHost(mine, FakeVoice(status)), cache)
                self.assertEqual((done.error, done.audio), (word, None))
        ok = await self.run_bot(FakeHost(mine, FakeVoice()), cache)
        self.assertEqual(ok.error, "")

    async def test_the_store_claims_finishes_and_sweeps(self) -> None:
        store = bot.HearStore(self.db, clock=lambda: NOW)
        press = await self.press()
        item = await store.claim_next(THURSDAY.id)
        assert item is not None
        self.assertEqual((item.id, item.text, item.user_id), (press, WORDS, ALICE.id))
        self.assertIsNone(await store.claim_next(THURSDAY.id))  # taken
        self.assertTrue(await store.finish(THURSDAY.id, item, bot.Outcome(audio=b"clip")))
        seen = await read_hear(self.web, self.session, self.cref, press)
        self.assertEqual(base64.b64decode(seen["audio"]), b"clip")
        stale = bot.HearStore(self.db, clock=lambda: NOW + bot.KEEP_FOR_S + 1000)
        self.assertEqual(await stale.sweep(THURSDAY.id), 1)

    async def test_a_made_clip_adds_its_credits_once_and_a_cache_hit_adds_nothing(self) -> None:
        voice, cache = FakeVoice(), bot.HearCache()
        host = FakeHost(self.campaign(), voice)
        item = bot.Hear("a" * 32, self.one.id, ALICE.id, WORDS, 1)
        first = await bot.apply(host, cache, THURSDAY.id, item)
        self.assertEqual((host.used, first.credits), ({self.one.id: len(WORDS)}, len(WORDS)))
        await bot.apply(host, cache, THURSDAY.id, item)  # the same words: the cache answers
        self.assertEqual((host.used, voice.calls), ({self.one.id: len(WORDS)}, 1))

    async def test_an_used_up_month_says_voice_off_and_makes_no_call(self) -> None:
        voice = FakeVoice()
        host = FakeHost(self.campaign(), voice)
        host.used[self.one.id] = host.hear_limit
        item = bot.Hear("a" * 32, self.one.id, ALICE.id, WORDS, 1)
        done = await bot.apply(host, bot.HearCache(), THURSDAY.id, item)
        self.assertEqual((done.error, voice.calls), (bot.VOICE_OFF, 0))
        other = bot.Hear("b" * 32, self.two.id, ALICE.id, WORDS, 1)  # another campaign's month
        again = await bot.apply(host, bot.HearCache(), THURSDAY.id, other)
        self.assertEqual((again.error, voice.calls), ("", 1))

    async def test_the_months_count_is_per_campaign_and_resets_with_the_month(self) -> None:
        now = [float(calendar.timegm((2026, 11, 8, 12, 0, 0)))]
        store = bot.HearStore(self.db, clock=lambda: now[0])
        await store.spent(THURSDAY.id, self.one.id, 40)
        await store.spent(THURSDAY.id, self.one.id, 25)
        self.assertEqual(await store.used(THURSDAY.id, self.one.id), 65)
        self.assertEqual(await store.used(THURSDAY.id, self.two.id), 0)  # not another campaign's
        now[0] += 30 * 86400  # December
        self.assertEqual(await store.used(THURSDAY.id, self.one.id), 0)
        await store.spent(THURSDAY.id, self.one.id, 7)
        self.assertEqual(await store.used(THURSDAY.id, self.one.id), 7)
        async with self.db.guild(THURSDAY.id) as conn:  # November's row was cleared with it
            cur = await conn.execute(
                "SELECT month FROM hear_use WHERE campaign_id = %s", (self.one.id,)
            )
            self.assertEqual([r["month"] for r in await cur.fetchall()], [202612])

    async def test_the_month_is_the_utc_calendar_month(self) -> None:
        edge = calendar.timegm((2026, 12, 1, 0, 0, 0))
        self.assertEqual(bot.month_of(edge - 1), 202611)  # 30 Nov 23:59:59
        self.assertEqual(bot.month_of(edge), 202612)  # 1 Dec 00:00:00

    async def test_the_cache_is_one_campaigns_own(self) -> None:
        voice, cache = FakeVoice(), bot.HearCache()
        host = FakeHost(self.campaign(), voice)
        for campaign in (self.one.id, self.two.id):
            item = bot.Hear("a" * 32, campaign, ALICE.id, WORDS, 1)
            await bot.apply(host, cache, THURSDAY.id, item)
            await bot.apply(host, cache, THURSDAY.id, item)
        self.assertEqual(voice.calls, 2)  # one call each, the repeat in each is free
        other = bot.Hear("a" * 32, self.one.id, ALICE.id, WORDS, 1)
        await bot.apply(host, cache, THURSDAY.id + 1, other)  # another server
        self.assertEqual(voice.calls, 3)

    async def test_the_cache_forgets_by_age_and_by_count(self) -> None:
        now = [1000.0]
        cache = bot.HearCache(clock=lambda: now[0])
        cache.put("a", b"x")
        now[0] += bot.CACHE_FOR_S + 1
        self.assertIsNone(cache.get("a"))
        for n in range(bot.CACHE_MAX + 5):
            cache.put(str(n), b"x")
        self.assertIsNone(cache.get("0"))
        self.assertEqual(cache.get(str(bot.CACHE_MAX + 4)), b"x")

    async def test_a_stalled_press_is_retaken_once_and_then_fails(self) -> None:
        clock = [float(NOW)]
        store = bot.HearStore(self.db, clock=lambda: clock[0])
        await self.press()
        first = await store.claim_next(THURSDAY.id)
        assert first is not None
        clock[0] += bot.RETAKE_AFTER_S + 5  # the first worker stopped
        second = await store.claim_next(THURSDAY.id)
        assert second is not None
        self.assertEqual((first.tries, second.tries), (1, 2))
        self.assertFalse(await store.finish(THURSDAY.id, first, bot.Outcome(audio=b"late")))
        clock[0] += bot.RETAKE_AFTER_S + 5
        third = await store.claim_next(THURSDAY.id)
        assert third is not None
        voice = FakeVoice()
        done = await bot.apply(
            FakeHost(self.campaign(), voice), bot.HearCache(), THURSDAY.id, third
        )
        self.assertEqual((done.error, voice.calls), (bot.FAILED, 0))  # not spoken a third time

    async def test_ten_waiting_at_once_is_the_most(self) -> None:
        for _ in range(10):
            await self.press()
        with self.assertRaises(RecapError) as caught:
            await self.press()
        self.assertEqual(caught.exception.code, "too_soon")

    async def test_the_doors_answer_only_a_dm_of_that_campaign(self) -> None:
        async with self.web.user(BOB.id, session=self.bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_hear_add(%s, %s, 'x') AS a, dmbot_web_hear_read(%s, %s, %s) AS r",
                (THURSDAY.id, self.one.id, THURSDAY.id, self.one.id, "0" * 32),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["a"], row["r"]), (None, None))
        self.assertEqual(await self.rows(), [])

    async def test_the_words_never_reach_a_log_line(self) -> None:
        secret = "Zorblaxx"
        store = bot.HearStore(self.db)
        await self.press(secret)
        item = await store.claim_next(THURSDAY.id)
        assert item is not None
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("dmbot").info("start")  # assertLogs needs at least one line
            await bot.run_one(FakeHost(self.campaign(), FakeVoice()), store, bot.HearCache(),
                              THURSDAY.id, item)  # fmt: skip
            await bot.run_one(FakeHost(self.campaign(), FakeVoice(500)), store, bot.HearCache(),
                              THURSDAY.id, item)  # fmt: skip
        self.assertNotIn(secret, "\n".join(logs.output))


class OverHttp(Hearing):
    """The two routes, signed in as the DM."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(recap_audio_on=True), self.web, FakeDiscord(), clock=lambda: NOW
        )
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")

    async def test_press_then_poll(self) -> None:
        base = f"/me/campaigns/{self.cref}/names"
        queued = await self.client.post(
            f"{base}/{self.main}/said-as/hear", json={"text": WORDS}, headers=HEADERS
        )
        self.assertEqual(queued.status_code, 200)
        press = queued.json()["id"]
        polled = await self.client.get(f"{base}/{self.main}/said-as/hear/{press}")
        self.assertEqual((polled.status_code, polled.json()["state"]), (200, "waiting"))
        for body, status, error in (
            ({"text": "no 1"}, 422, "bad_text"),
            ({}, 422, "bad_text"),
        ):
            bad = await self.client.post(
                f"{base}/{self.main}/said-as/hear", json=body, headers=HEADERS
            )
            self.assertEqual((bad.status_code, bad.json()), (status, {"error": error}))
        secret = await self.client.post(
            f"{base}/{self.secret}/said-as/hear", json={"text": WORDS}, headers=HEADERS
        )
        self.assertEqual(secret.status_code, 404)
        missing = await self.client.get(f"{base}/{self.main}/said-as/hear/{'0' * 32}")
        self.assertEqual(missing.status_code, 404)

    async def test_voice_off_when_the_server_has_no_voice(self) -> None:
        app = create_app(settings(), self.web, FakeDiscord(), clock=lambda: NOW)
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        )
        self.addAsyncCleanup(client.aclose)
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        off = await client.post(
            f"/me/campaigns/{self.cref}/names/{self.main}/said-as/hear",
            json={"text": WORDS},
            headers=HEADERS,
        )
        self.assertEqual((off.status_code, off.json()), (409, {"error": "voice_off"}))
