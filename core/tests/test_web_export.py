"""The data export (#1150), as the website's own database role: the signed-in person's own
rows only, never another person's, a rate limit, and nothing for someone signed out."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import errors

from dmbot import consent as consent_module
from dmbot.account_id import to_dmbot_id
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.db import Database
from dmbot.memory import sheets
from dmbot.memory.backup import MemorySection
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.store import MemoryStore
from dmbot.web import entitlements_writer, grants, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.export import build_export
from dmbot.web.limits import RateLimit
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
HOOK_SECRET = b"h" * 32


class Export(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        # The bot's side writes what the website will read.
        campaigns = CampaignStore(self.db, clock=lambda: self.now)
        campaigns.register_section(MemorySection())
        self.mine = await campaigns.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        self.bobs = await campaigns.create(THURSDAY.id, "Bob's Own Game", BOB.id)
        self.consent = ConsentStore(self.db)
        await self.consent.grant(THURSDAY.id, ALICE.id)
        await self.consent.grant(THURSDAY.id, BOB.id)
        await self.consent.grant(GORRAK.id, ALICE.id)  # in her list, no campaign there
        memory = MemoryStore(self.db, clock=lambda: self.now)
        pc = await memory.add_entity(
            THURSDAY.id,
            self.mine.id,
            name="Testa",
            type="player_character",
            source="dm",
            played_by=ALICE.id,
        )
        other = await memory.add_entity(
            THURSDAY.id,
            self.bobs.id,
            name="Secretus",
            type="player_character",
            source="dm",
            played_by=BOB.id,
        )
        store = SheetStore(self.db)
        typed = sheets.typed("Testa", species="Elf", class_name="Bard", level=2, names=["Song"])
        assert typed is not None
        await store.save(THURSDAY.id, self.mine.id, pc.value.id, typed, 5)
        theirs = sheets.typed("Secretus", species="Orc", class_name="Rogue", level=3, names=[])
        assert theirs is not None
        await store.save(THURSDAY.id, self.bobs.id, other.value.id, theirs, 6)
        self.alice = await self.sign_in(ALICE)
        self.bob = await self.sign_in(BOB)
        # One person's payment rows each: the plan writer may only write its own person's.
        for person in (ALICE, BOB):
            async with self.db.plan_writer(person.id) as conn:
                await conn.execute(
                    "INSERT INTO payment_events (provider, event_id, user_id, kind, received_at)"
                    " VALUES ('fake', %s, %s, 'subscription_started', %s)",
                    (f"evt-{person.id}", person.id, self.now),
                )
        # Bob plays a character in Alice's campaign without being its DM.
        self.cameo = await memory.add_entity(
            THURSDAY.id,
            self.mine.id,
            name="Cameo",
            type="player_character",
            source="dm",
            played_by=BOB.id,
        )
        cameo = sheets.typed("Cameo", species="Dwarf", class_name="Monk", level=1, names=[])
        assert cameo is not None
        await store.save(THURSDAY.id, self.mine.id, self.cameo.value.id, cameo, 7)

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def sign_in(self, user: DiscordUser) -> sessions.Session:
        token = await sessions.sign_in(
            self.web, user, [THURSDAY, QUILLON, GORRAK], now=self.now, days=30
        )
        found = await sessions.find(self.web, token, now=self.now)
        assert found is not None
        return found

    async def test_it_holds_the_persons_own_data(self) -> None:
        data = await build_export(self.web, self.alice, now=self.now)
        self.assertEqual(data["account"]["discordId"], str(ALICE.id))
        self.assertEqual(data["account"]["dmbotId"], to_dmbot_id(ALICE.id))
        self.assertEqual(data["account"]["previousDiscordIds"], [])  # never moved
        self.assertIsNone(data["account"]["paymentEmail"])  # None until a payment has come
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'payer@example.com' WHERE user_id = %s",
                (ALICE.id,),
            )
        again = await build_export(self.web, self.alice, now=self.now)
        self.assertEqual(again["account"]["paymentEmail"], "payer@example.com")
        self.assertEqual(data["account"]["name"], ALICE.name)
        self.assertEqual(
            sorted(c["serverName"] for c in data["recordingConsent"]),
            sorted([THURSDAY.name, GORRAK.name]),
        )
        self.assertEqual(
            {c["wordingVersion"] for c in data["recordingConsent"]}, {consent_module.TERMS_VERSION}
        )
        self.assertEqual([c["name"] for c in data["campaigns"]], [self.mine.name])
        self.assertEqual(data["campaigns"][0]["role"], "owner")
        character = next(c for c in data["characters"] if c["campaign"] == self.mine.name)
        self.assertEqual((character["campaign"], character["typedIn"]), (self.mine.name, True))
        self.assertEqual([p["kind"] for p in data["paymentEvents"]], ["subscription_started"])

    async def test_another_persons_data_is_absent(self) -> None:
        text = json.dumps(await build_export(self.web, self.alice, now=self.now))
        for sentence in ("Bob's Own Game", "Secretus", str(BOB.id), BOB.email or "", "Orc"):
            self.assertNotIn(sentence, text)
        bobs = await build_export(self.web, self.bob, now=self.now)
        self.assertEqual([c["name"] for c in bobs["campaigns"]], [self.bobs.name])
        self.assertNotIn("Testa", json.dumps(bobs))
        self.assertEqual(len(bobs["paymentEvents"]), 1)

    async def test_only_servers_in_the_sessions_list_are_read(self) -> None:
        # Consent in a server the session doesn't list is never read, even for her own row.
        await self.consent.grant(680000000000000444, ALICE.id)
        data = await build_export(self.web, self.alice, now=self.now)
        self.assertNotIn("680000000000000444", json.dumps(data))
        self.assertEqual(len(data["recordingConsent"]), 2)

    async def test_a_sheet_in_a_campaign_the_person_does_not_dm_still_names_it(self) -> None:
        data = await build_export(self.web, self.bob, now=self.now)
        by_campaign = {c["campaign"] for c in data["characters"]}
        self.assertEqual(by_campaign, {self.bobs.name, self.mine.name})
        # Only the campaigns of his own sheets: not the server's other campaigns.
        self.assertEqual({c["name"] for c in data["campaigns"]}, {self.bobs.name})

    async def test_the_policies_hold_the_role_even_without_a_where_clause(self) -> None:
        async def rows(
            user: DiscordUser, session: sessions.Session, guild: int, query: str
        ) -> list[int]:
            async with self.web.user(user.id, session=session.id_hash) as conn:
                await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild),))
                cur = await conn.execute(query)
                return [r["who"] for r in await cur.fetchall()]

        sheets_sql = "SELECT player_id AS who FROM character_sheets"
        consent_sql = "SELECT user_id AS who FROM consent"
        # Same server, whole table: only the signed-in person's own rows.
        self.assertEqual(await rows(ALICE, self.alice, THURSDAY.id, consent_sql), [ALICE.id])
        self.assertEqual(await rows(ALICE, self.alice, THURSDAY.id, sheets_sql), [ALICE.id])
        self.assertEqual(
            sorted(await rows(BOB, self.bob, THURSDAY.id, sheets_sql)), [BOB.id, BOB.id]
        )
        # A server outside the session's list: nothing, her own consent included.
        await self.consent.grant(680000000000000444, ALICE.id)
        self.assertEqual(await rows(ALICE, self.alice, 680000000000000444, consent_sql), [])
        # A session whose list leaves Thursday out sees nothing there, with the server set.
        token = await sessions.sign_in(self.web, ALICE, [GORRAK], now=self.now, days=30)
        narrow = await sessions.find(self.web, token, now=self.now)
        assert narrow is not None
        self.assertEqual(await rows(ALICE, narrow, THURSDAY.id, consent_sql), [])
        self.assertEqual(await rows(ALICE, narrow, THURSDAY.id, sheets_sql), [])

    async def test_free_access_is_in_the_export_without_who_gave_it(self) -> None:
        admin = "admin@example.invalid"
        await grants.give(
            self.db, admin, ALICE.id, "guild", ends_at=None, note="a private note", now=self.now
        )
        data = await build_export(self.web, self.alice, now=self.now)
        self.assertEqual(
            (data["freeAccess"]["level"], data["freeAccess"]["endsAt"]), ("guild", None)
        )
        self.assertTrue(data["freeAccess"]["givenAt"])
        text = json.dumps(data)
        self.assertNotIn(admin, text)
        self.assertNotIn("a private note", text)
        bobs = await build_export(self.web, self.bob, now=self.now)
        self.assertIsNone(bobs["freeAccess"])

    async def test_the_plan_is_in_the_export(self) -> None:
        started = await entitlements_writer.start_try_it(self.web, ALICE.id, now=self.now)
        self.assertTrue(started.started)
        data = await build_export(self.web, self.alice, now=self.now)
        self.assertEqual((data["plan"]["plan"], data["usedTryIt"]), ("try-it", True))

    async def test_the_role_reads_only_its_own_consent_and_may_only_delete_it(self) -> None:
        async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("SELECT user_id FROM consent")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE.id])
        # No yes can be added or changed from the website (the right to stop is the only one,
        # #1151: the website's Stop recording me).
        for statement in (
            "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (111, 1001, 1)",
            "UPDATE consent SET granted_at = granted_at + 1",
        ):
            async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
                await conn.execute(
                    "SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),)
                )
                with self.assertRaises(errors.InsufficientPrivilege):
                    await conn.execute(statement)
        # A delete with no WHERE takes only her own row here, never Bob's.
        async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("DELETE FROM consent RETURNING user_id")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE.id])
        self.assertIsNotNone(await self.consent.granted_at(THURSDAY.id, BOB.id))
        async with self.web.unscoped() as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("SELECT 1 FROM transcript_lines")  # still closed


class ExportRoute(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.limit = RateLimit(60, clock=lambda: float(self.now))
        self.app = create_app(
            settings(),
            self.db,
            FakeDiscord(),
            payments=FakeProvider(HOOK_SECRET, SITE),
            export_limit=self.limit,
            clock=lambda: self.now,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def sign_in(self) -> None:
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def test_signed_out_is_refused(self) -> None:
        self.assertEqual((await self.client.get("/me/export")).status_code, 401)

    async def test_it_downloads_a_json_file_once_a_minute(self) -> None:
        await self.sign_in()
        first = await self.client.get("/me/export")
        self.assertEqual(first.status_code, 200)
        self.assertIn("attachment", first.headers["content-disposition"])
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertEqual(first.json()["account"]["discordId"], str(ALICE.id))
        again = await self.client.get("/me/export")
        self.assertEqual((again.status_code, again.json()), (429, {"error": "slow_down"}))
        self.now += 61
        self.assertEqual((await self.client.get("/me/export")).status_code, 200)

    async def test_the_log_says_it_happened_and_nothing_more(self) -> None:
        await self.sign_in()
        with self.assertLogs("dmbot.web.app", "INFO") as logged:
            await self.client.get("/me/export")
        text = " ".join(logged.output)
        self.assertIn("data export", text)
        for secret in (ALICE.name, ALICE.email or "", str(ALICE.id)):
            self.assertNotIn(secret, text)
