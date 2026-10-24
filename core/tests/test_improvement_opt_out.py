"""The improvement opt-out (#1609): the choice is kept per person and read one person at a time;
the website's route reads and saves only the signed-in person's own; every path that takes
people's words out for improvement skips an opted-out person, read at the time. The store tests
need Postgres, the route tests also the website's role (DMBOT_TEST_SUPERUSER_URL)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
from psycopg import errors

from dmbot.consent import ConsentStore
from dmbot.db import Database
from dmbot.transcript import improvement
from dmbot.transcript.improvement import OptOutStore
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, SITE, FakeDiscord, settings
from tests.test_web_recording import HEADERS
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
CAROL = 3003


@dataclass
class Said:
    user_id: int | None
    text: str


class Choice(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = OptOutStore(self.db)

    async def test_nobody_is_opted_out_until_they_choose(self) -> None:
        self.assertFalse(await self.store.is_opted_out(ALICE.id))
        self.assertEqual(await self.store.allowed([ALICE.id, BOB.id]), {ALICE.id, BOB.id})

    async def test_opting_out_and_back_in(self) -> None:
        await self.store.set(ALICE.id, improve=False)
        self.assertTrue(await self.store.is_opted_out(ALICE.id))
        await self.store.set(ALICE.id, improve=False)  # twice is fine
        self.assertTrue(await self.store.is_opted_out(ALICE.id))
        await self.store.set(ALICE.id, improve=True)
        self.assertFalse(await self.store.is_opted_out(ALICE.id))

    async def test_it_is_one_persons_choice_not_everyones(self) -> None:
        await self.store.set(ALICE.id, improve=False)
        self.assertEqual(await self.store.allowed([ALICE.id, BOB.id]), {BOB.id})
        self.assertEqual(await self.store.opted_out([ALICE.id, BOB.id]), {ALICE.id})

    async def test_a_person_sees_only_their_own_row(self) -> None:
        await self.store.set(ALICE.id, improve=False)
        await self.store.set(BOB.id, improve=False)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT user_id FROM improvement_opt_outs")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE.id])
        async with self.db.guild(111) as conn:  # a server's transaction sees no person's rows
            cur = await conn.execute("SELECT count(*) AS n FROM improvement_opt_outs")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_nobody_can_write_another_persons_choice(self) -> None:
        async with self.db.user(ALICE.id) as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute(
                    "INSERT INTO improvement_opt_outs (user_id, opted_out_at) VALUES (%s, 1)",
                    (BOB.id,),
                )

    async def test_the_choice_is_read_again_each_time(self) -> None:
        self.assertEqual(await improvement.allowed(self.db, [ALICE.id]), {ALICE.id})
        await self.store.set(ALICE.id, improve=False)
        self.assertEqual(await improvement.allowed(self.db, [ALICE.id]), set())  # a new store
        self.assertEqual(await improvement.allowed(self.db, [ALICE.id, BOB.id]), {BOB.id})

    async def test_a_line_that_cannot_say_whose_it_is_is_refused(self) -> None:
        with self.assertRaises(AttributeError):
            await improvement.filter_lines(self.db, [SimpleNamespace(text="x")])

    async def test_lines_of_an_opted_out_person_are_left_out_and_others_kept_in_order(self) -> None:
        await self.store.set(BOB.id, improve=False)
        lines = [Said(ALICE.id, "a"), Said(BOB.id, "b"), Said(None, "n"), Said(CAROL, "c")]
        kept = await improvement.filter_lines(self.db, lines)
        self.assertEqual([x.text for x in kept], ["a", "n", "c"])

    async def test_a_recently_read_answer_is_trusted_for_a_few_seconds_only(self) -> None:
        now = [1000.0]
        store = OptOutStore(self.db, clock=lambda: now[0])
        self.assertFalse(await store.is_opted_out_recently(ALICE.id))
        await OptOutStore(self.db).set(ALICE.id, improve=False)  # chosen on the website
        self.assertFalse(await store.is_opted_out_recently(ALICE.id))  # (still the last answer)
        now[0] += improvement.CACHE_S + 1
        self.assertTrue(await store.is_opted_out_recently(ALICE.id))

    async def test_deleting_the_people_does_not_touch_the_choice(self) -> None:
        # No link to the account: forgetting the choice would put their words back in.
        await self.store.set(ALICE.id, improve=False)
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_constraint WHERE contype = 'f'"
                " AND conrelid = 'improvement_opt_outs'::regclass"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class Recorder(DatabaseTest):
    """The live test recorder (bot.py `_save_test_audio`) skips an opted-out person."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.bot import DMBot
        from dmbot.campaigns import CampaignStore
        from dmbot.config import Settings
        from dmbot.sessions import SessionStore
        from dmbot.test_recording.store import TestVoiceStore

        self.optouts = OptOutStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            CampaignStore(self.db),
            SessionStore(self.db),
            test_voice=TestVoiceStore(self.db),
            improvement=self.optouts,
        )
        self.bot.consent.has_consent = lambda guild_id, user_id: True  # type: ignore[method-assign]
        assert self.bot.test_voice is not None
        self.bot.test_voice.has = lambda guild_id, user_id: True  # type: ignore[method-assign]
        self.session = SimpleNamespace(
            add_utterance=AsyncMock(),
            has=lambda user_id: True,
            forget=MagicMock(),
            gap=MagicMock(),
            people=lambda: [ALICE.id, BOB.id],
            finish=MagicMock(),
        )
        self.table = SimpleNamespace(
            guild_id=111, test_session=self.session, is_dm=lambda user_id: False
        )

    def utterance(self, user_id: int) -> Any:
        return SimpleNamespace(user_id=user_id, start_ms=0, end_ms=1000, pcm=b"\0\0")

    async def test_someone_who_did_not_opt_out_is_saved(self) -> None:
        await self.bot._save_test_audio(cast(Any, self.table), self.utterance(ALICE.id), "hi")
        self.session.add_utterance.assert_awaited_once()

    async def test_someone_who_opted_out_is_not_saved_and_what_was_saved_goes(self) -> None:
        await self.optouts.set(ALICE.id, improve=False)
        await self.bot._save_test_audio(cast(Any, self.table), self.utterance(ALICE.id), "hi")
        self.session.add_utterance.assert_not_awaited()
        self.session.forget.assert_called_once_with(ALICE.id)

    async def test_opting_out_mid_session_stops_the_saving_within_seconds(self) -> None:
        now = [1000.0]
        self.optouts._now = lambda: now[0]
        await self.bot._save_test_audio(cast(Any, self.table), self.utterance(ALICE.id), "one")
        await OptOutStore(self.db).set(ALICE.id, improve=False)
        now[0] += improvement.CACHE_S + 1
        await self.bot._save_test_audio(cast(Any, self.table), self.utterance(ALICE.id), "two")
        self.assertEqual(self.session.add_utterance.await_count, 1)

    async def test_when_the_choice_cannot_be_read_nothing_is_saved(self) -> None:
        self.optouts.is_opted_out_recently = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.bot._save_test_audio(cast(Any, self.table), self.utterance(ALICE.id), "hi")
        self.session.add_utterance.assert_not_awaited()

    async def test_someone_who_opts_out_and_never_speaks_again_is_dropped_at_the_end(self) -> None:
        await self.optouts.set(BOB.id, improve=False)
        await self.bot._purge_opted_out(cast(Any, self.session))
        self.session.forget.assert_called_once_with(BOB.id)

    async def test_the_end_of_a_session_drops_a_speaker_whose_choice_cannot_be_read(self) -> None:
        self.optouts.is_opted_out = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.bot._purge_opted_out(cast(Any, self.session))
        self.assertEqual(self.session.forget.call_count, 2)

    async def test_finishing_the_session_purges_before_it_writes_the_last_manifest(self) -> None:
        await self.optouts.set(ALICE.id, improve=False)
        await self.bot._finish_test_session(cast(Any, self.table), True)
        self.session.forget.assert_called_once_with(ALICE.id)
        self.session.finish.assert_called_once()

    async def test_no_test_session_starts_without_the_opt_out_store(self) -> None:
        self.bot.improvement = None
        self.table.test_session = None
        self.bot.settings = SimpleNamespace(  # type: ignore[assignment]
            test_recording_guilds={111}, test_recordings_dir="/nonexistent"
        )
        await self.bot._start_test_session(cast(Any, self.table))
        self.assertIsNone(self.table.test_session)

    async def test_one_persons_opt_out_does_not_stop_anyone_else(self) -> None:
        await self.optouts.set(ALICE.id, improve=False)
        await self.bot._save_test_audio(cast(Any, self.table), self.utterance(BOB.id), "hi")
        self.session.add_utterance.assert_awaited_once()


class Route(DatabaseTest):
    """`GET/PUT /me/improvement`, as the website's own database role."""

    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.discord = FakeDiscord()
        self.app = create_app(
            settings(),
            self.web,
            self.discord,
            payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: self.now,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlsplit

        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.web.close()
        await super().asyncTearDown()

    async def test_the_default_is_that_dmbot_may_use_their_words(self) -> None:
        got = await self.client.get("/me/improvement")
        self.assertEqual((got.status_code, got.json()), (200, {"improve": True}))

    async def test_turning_it_off_and_on(self) -> None:
        off = await self.client.put("/me/improvement", json={"improve": False}, headers=HEADERS)
        self.assertEqual((off.status_code, off.json()), (200, {"improve": False}))
        self.assertEqual((await self.client.get("/me/improvement")).json(), {"improve": False})
        self.assertTrue(await OptOutStore(self.db).is_opted_out(ALICE.id))
        on = await self.client.put("/me/improvement", json={"improve": True}, headers=HEADERS)
        self.assertEqual(on.json(), {"improve": True})
        self.assertFalse(await OptOutStore(self.db).is_opted_out(ALICE.id))

    async def test_it_only_ever_changes_the_signed_in_persons_own_choice(self) -> None:
        await OptOutStore(self.db).set(BOB.id, improve=False)
        await self.client.put("/me/improvement", json={"improve": True}, headers=HEADERS)
        self.assertTrue(await OptOutStore(self.db).is_opted_out(BOB.id))  # Bob's is untouched
        got = await self.client.get("/me/improvement")
        self.assertEqual(got.json(), {"improve": True})  # and Alice never sees Bob's

    async def test_a_body_that_is_not_a_yes_or_a_no_is_refused(self) -> None:
        for body in ({}, {"improve": "no"}, {"improve": 0}, {"optedOut": True}, [True], None):
            got = await self.client.put("/me/improvement", json=body, headers=HEADERS)
            self.assertEqual(got.status_code, 422, body)
        self.assertEqual((await self.client.get("/me/improvement")).json(), {"improve": True})

    async def test_it_needs_the_site_header_and_a_signed_in_person(self) -> None:
        plain = await self.client.put("/me/improvement", json={"improve": False})
        self.assertEqual(plain.status_code, 403)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        ) as stranger:
            self.assertEqual((await stranger.get("/me/improvement")).status_code, 401)
            put = await stranger.put("/me/improvement", json={"improve": False}, headers=HEADERS)
            self.assertEqual(put.status_code, 401)
        self.assertFalse(await OptOutStore(self.db).is_opted_out(ALICE.id))

    async def test_the_website_role_cannot_touch_anyone_elses_row(self) -> None:
        await OptOutStore(self.db).set(BOB.id, improve=False)
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT user_id FROM improvement_opt_outs")
            self.assertEqual(await cur.fetchall(), [])  # Bob's row is not hers to see
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute(
                    "INSERT INTO improvement_opt_outs (user_id, opted_out_at) VALUES (%s, 1)",
                    (BOB.id,),
                )
