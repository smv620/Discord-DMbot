"""My characters (#1164), as the website's own database role: the person's own sheets only,
read per server from the session's list, never another player's."""

from __future__ import annotations

import json
import unittest
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot.campaigns import CampaignStore
from dmbot.db import Database
from dmbot.memory import sheets
from dmbot.memory.backup import MemorySection
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.store import MemoryStore
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.characters import build_characters, class_line
from dmbot.web.discord import DiscordUser
from dmbot.web.export import order_sheet_rows, read_sheet_rows
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
OUTSIDE = 4444


class Characters(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        campaigns = CampaignStore(self.db, clock=lambda: self.now)
        campaigns.register_section(MemorySection())
        self.mine = await campaigns.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        self.bobs = await campaigns.create(THURSDAY.id, "Bob's Own Game", BOB.id)
        self.far = await campaigns.create(OUTSIDE, "A Far Game", ALICE.id)
        memory = MemoryStore(self.db, clock=lambda: self.now)
        store = SheetStore(self.db)

        async def character(guild: int, campaign: str, name: str, player: int) -> str:
            made = await memory.add_entity(
                guild, campaign, name=name, type="player_character", source="dm", played_by=player
            )
            return made.value.id

        async def typed(guild: int, campaign: str, name: str, player: int, cls: str) -> None:
            entity = await character(guild, campaign, name, player)
            snapshot = sheets.typed(name, species="Elf", class_name=cls, level=2, names=[])
            assert snapshot is not None
            await store.save(guild, campaign, entity, snapshot, 5)

        await typed(THURSDAY.id, self.mine.id, "Testa", ALICE.id, "Bard")
        await typed(THURSDAY.id, self.mine.id, "Cameo", BOB.id, "Monk")  # Bob, in Alice's game
        await typed(THURSDAY.id, self.bobs.id, "Secretus", BOB.id, "Rogue")
        await typed(OUTSIDE, self.far.id, "Faraway", ALICE.id, "Druid")  # a server not on her list
        linked = await character(THURSDAY.id, self.mine.id, "Linky", ALICE.id)
        await store.link(THURSDAY.id, self.mine.id, linked, 12345678)  # a link, not read yet
        self.alice = await self.sign_in(ALICE)
        self.bob = await self.sign_in(BOB)

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

    async def test_she_sees_only_her_own_sheets_never_bobs_in_the_same_campaign(self) -> None:
        cards = await build_characters(self.web, self.alice)
        # The linked sheet has no name yet, so it shows as None.
        self.assertEqual(sorted(str(c["name"]) for c in cards), ["None", "Testa"])
        text = json.dumps(cards)
        for other in ("Cameo", "Secretus", "Monk", "Rogue", str(BOB.id)):
            self.assertNotIn(other, text)

    async def test_he_sees_his_own_in_both_campaigns(self) -> None:
        cards = await build_characters(self.web, self.bob)
        self.assertEqual(
            {(c["name"], c["campaign"]) for c in cards},
            {("Cameo", self.mine.name), ("Secretus", self.bobs.name)},
        )

    async def test_a_server_outside_the_sessions_list_is_not_read(self) -> None:
        cards = await build_characters(self.web, self.alice)
        self.assertNotIn("Faraway", json.dumps(cards))
        self.assertNotIn(self.far.name, json.dumps(cards))

    async def test_a_sheet_with_no_class_line_still_shows(self) -> None:
        cards = await build_characters(self.web, self.alice)
        (linked,) = [c for c in cards if c["sheetLink"]]
        self.assertEqual(
            (linked["name"], linked["classLine"], linked["typedIn"], linked["readAt"]),
            (None, None, False, None),
        )
        self.assertIn("12345678", linked["sheetLink"])

    async def test_a_typed_sheet_has_its_fields(self) -> None:
        cards = await build_characters(self.web, self.alice)
        (typed,) = [c for c in cards if c["name"] == "Testa"]
        self.assertEqual(
            (typed["classLine"], typed["level"], typed["typedIn"], typed["campaign"]),
            ("Bard 2", 2, True, self.mine.name),
        )
        self.assertEqual(typed["serverName"], THURSDAY.name)
        self.assertIsNone(typed["sheetLink"])
        self.assertTrue(typed["readAt"])
        # Exactly these fields and no others: nothing else of the snapshot can leak.
        self.assertEqual(
            set(typed),
            {
                "serverName",
                "campaign",
                "name",
                "classLine",
                "level",
                "maxHp",
                "ac",
                "sheetLink",
                "typedIn",
                "readAt",
            },
        )

    async def test_the_database_holds_the_role_whatever_the_code_asks(self) -> None:
        # Not just the loop over her servers: a read aimed straight at a server not on her
        # list, or at someone else's rows, finds nothing.
        async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(OUTSIDE),))
            self.assertEqual(await read_sheet_rows(conn, OUTSIDE, ALICE.id), [])
        async with self.web.user(ALICE.id, session=self.alice.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            self.assertEqual(await read_sheet_rows(conn, THURSDAY.id, BOB.id), [])
            names = {r["campaign"] for r in await read_sheet_rows(conn, THURSDAY.id, ALICE.id)}
            self.assertEqual(names, {self.mine.name})


class Order(unittest.TestCase):
    def test_cards_come_in_campaign_name_order_whatever_the_ids_are(self) -> None:
        # Ids in the reverse of name order: only a sort by name puts these right.
        rows: list[dict[str, Any]] = [
            {"campaign": "Zed's Game", "campaign_id": "a" * 32, "entity_id": "e1"},
            {"campaign": "Alpha Game", "campaign_id": "z" * 32, "entity_id": "e2"},
            {"campaign": None, "campaign_id": "m" * 32, "entity_id": "e3"},
            {"campaign": "Alpha Game", "campaign_id": "z" * 32, "entity_id": "e0"},
        ]
        ordered = order_sheet_rows(rows)
        self.assertEqual([r["entity_id"] for r in ordered], ["e3", "e0", "e2", "e1"])


class Words(unittest.TestCase):
    def test_the_class_line_joins_classes(self) -> None:
        line = class_line(
            {"classes": [{"name": "Ranger", "level": 3}, {"name": "Druid", "level": 3}]}
        )
        self.assertEqual(line, "Ranger 3 / Druid 3")
        self.assertIsNone(class_line({"classes": []}))
        self.assertIsNone(class_line(None))


class Route(DatabaseTest):
    async def test_it_needs_a_sign_in_and_answers_with_a_characters_list(self) -> None:
        app = create_app(
            settings(),
            self.db,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: 1_800_000_000,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        ) as client:
            self.assertEqual((await client.get("/me/characters")).status_code, 401)
            start = await client.get("/auth/discord/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            await client.get("/auth/discord/callback", params={"state": state, "code": "good-code"})
            answer = await client.get("/me/characters")
            self.assertEqual((answer.status_code, answer.json()), (200, {"characters": []}))
