"""The data export (#1150), as the website's own database role: the signed-in person's own
rows only, never another person's, a rate limit, and nothing for someone signed out."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import errors

from dmbot import consent as consent_module
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.db import Database
from dmbot.memory import sheets
from dmbot.memory.backup import MemorySection
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.store import MemoryStore
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.export import build_export
from dmbot.web.limits import RateLimit
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, API, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
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
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO payment_events (provider, event_id, user_id, kind, received_at)"
                " VALUES ('fake', 'a1', %s, 'subscription_started', %s),"
                " ('fake', 'b1', %s, 'subscription_started', %s)",
                (ALICE.id, self.now, BOB.id, self.now),
            )

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
        (character,) = data["characters"]
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
        await self.consent.grant(4444, ALICE.id)
        data = await build_export(self.web, self.alice, now=self.now)
        self.assertNotIn("4444", json.dumps(data))
        self.assertEqual(len(data["recordingConsent"]), 2)

    async def test_the_role_reads_only_its_own_consent_and_cannot_write_it(self) -> None:
        async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("SELECT user_id FROM consent")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE.id])
        async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("DELETE FROM consent")
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
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
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
        self.assertEqual((again.status_code, again.json()), (429, {"detail": "slow_down"}))
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
