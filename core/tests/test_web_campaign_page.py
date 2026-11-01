"""One campaign's own page (#2416): `GET /me/campaigns/{ref}` and the `ref` on `/me`'s campaigns.

A DM of the campaign is its "owner" or a "co-dm"; anyone else in the server is a "player"; a
person outside the server, a campaign that isn't there and another server's campaign all get the
same refusal. Needs the database (skipped without one, like the other web tests)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.campaign_page import read_campaign
from dmbot.web.me import build_me
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE as ALICE_USER
from tests.test_web_api import QUILLON, SITE, THURSDAY, FakeDiscord, settings

CAROL = SimpleNamespace(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM
DAVE = SimpleNamespace(id=4004, name="Dave", email="dave@example.com")  # not in the server at all


class TheCampaignPage(Base):
    async def session_of(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def test_the_owner_is_the_owner_and_a_co_dm_is_a_co_dm(self) -> None:
        got = await read_campaign(self.web, self.session, self.ref_of(self.one))
        self.assertEqual(
            (got["ref"], got["name"], got["role"], got["serverName"]),
            (self.ref_of(self.one), "One", "owner", THURSDAY.name),
        )
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.session_of(BOB)
        got = await read_campaign(self.web, bob, self.ref_of(self.one))
        self.assertEqual(got["role"], "co-dm")

    async def test_someone_else_in_the_server_is_a_player_of_a_campaign_they_do_not_run(
        self,
    ) -> None:
        # Bob owns "Two" and only plays in "One".
        bob = await self.session_of(BOB)
        got = await read_campaign(self.web, bob, self.ref_of(self.one))
        self.assertEqual((got["name"], got["role"]), ("One", "player"))
        got = await read_campaign(self.web, bob, self.ref_of(self.two))
        self.assertEqual(got["role"], "owner")
        carol = await self.session_of(CAROL)
        got = await read_campaign(self.web, carol, self.ref_of(self.one))
        self.assertEqual(got["role"], "player")

    async def test_nobody_outside_the_server_or_the_campaign_gets_anything(self) -> None:
        dave = await self.session_of(DAVE, [QUILLON])  # in another server only
        with self.assertRaises(RecapError) as refused:
            await read_campaign(self.web, dave, self.ref_of(self.one))
        self.assertEqual(refused.exception.code, "not_found")
        # A campaign that isn't there, a mangled address and another server's campaign.
        for ref in (f"{THURSDAY.id}.nope", "garbage", f"{QUILLON.id}.{self.one.id}"):
            with self.assertRaises(RecapError) as refused:
                await read_campaign(self.web, self.session, ref)
            self.assertEqual(refused.exception.code, "not_found")

    async def test_a_dm_gets_the_current_settings_and_a_player_gets_none(self) -> None:
        got = await read_campaign(self.web, self.session, self.ref_of(self.one))
        self.assertEqual(
            set(got["settings"]),
            {
                "visibility",
                "level",
                "keepDays",
                "paused",
                "storyMode",
                "dndbeyondInvite",
                "mainRules",
                "fallbackRules",
                "gameMasters",
            },
        )
        self.assertEqual(got["settings"]["gameMasters"], 1)
        self.assertIn(got["settings"]["visibility"], ("private", "peek", "open"))
        self.assertFalse(got["settings"]["paused"])
        self.assertEqual(got["settings"]["storyMode"], "off")  # the Shared story switch (#2985)
        self.assertIsNone(got["settings"]["keepDays"])  # kept for ever until the DM sets a time
        self.assertEqual(got["settings"]["level"], "normal")
        # A player of "One" (Bob only runs "Two") gets none; he is made a co-DM just below.
        bob = await self.session_of(BOB)
        self.assertNotIn("settings", await read_campaign(self.web, bob, self.ref_of(self.one)))
        self.assertIn("settings", await read_campaign(self.web, bob, self.ref_of(self.two)))
        # A co-DM gets them too, and each value comes from its own column.
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET transcript_keep_days = 90, paused = TRUE,"
                " dm_screen_level = 'quiet', dm_screen_visibility = 'private',"
                " story_mode = 'guide'"
                " WHERE guild_id = %s AND id = %s",
                (THURSDAY.id, self.one.id),
            )
        co = await read_campaign(self.web, bob, self.ref_of(self.one))
        self.assertEqual(
            co["settings"],
            {"visibility": "private", "level": "quiet", "keepDays": 90, "paused": True,
             "storyMode": "guide", "dndbeyondInvite": "", "mainRules": "2024",
             "fallbackRules": "2014", "gameMasters": 2},
        )  # fmt: skip

    async def test_importing_is_said_for_the_campaigns_server(self) -> None:
        got = await read_campaign(
            self.web, self.session, self.ref_of(self.one), imports_for=lambda g: g == THURSDAY.id
        )
        self.assertTrue(got["importsOn"])
        got = await read_campaign(self.web, self.session, self.ref_of(self.one))
        self.assertFalse(got["importsOn"])

    async def test_the_account_lists_each_campaign_with_its_address(self) -> None:
        me = await build_me(self.web, self.session, now=NOW)
        self.assertEqual([c["ref"] for c in me["campaigns"]], [self.ref_of(self.one)])

    def ref_of(self, campaign: Any) -> str:
        return f"{THURSDAY.id}.{campaign.id}"

    async def test_another_servers_campaign_is_not_found_by_the_database_itself(self) -> None:
        """The person is in both servers (so Python's own server check passes) and asks for a real
        campaign of one server under the other server's address: the door answers nothing."""
        quill = await self.campaigns.create(QUILLON.id, "Quill", BOB.id)
        both = await self.session_of(CAROL, [THURSDAY, QUILLON])
        for ref in (f"{QUILLON.id}.{self.one.id}", f"{THURSDAY.id}.{quill.id}"):
            with self.assertRaises(RecapError) as refused:
                await read_campaign(self.web, both, ref)
            self.assertEqual(refused.exception.code, "not_found", ref)
        got = await read_campaign(self.web, both, f"{QUILLON.id}.{quill.id}")
        self.assertEqual(
            (got["name"], got["role"], got["serverName"]), ("Quill", "player", QUILLON.name)
        )

    async def test_the_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_campaign_card"), {"schema.py", "web/campaign_page.py"})


class TheCampaignRoute(Base):
    """`GET /me/campaigns/{ref}` over HTTP: the shape, the refusal and signed out."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(imports_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            headers=HEADERS,
        )
        self.addAsyncCleanup(self.client.aclose)
        self.ref = f"{THURSDAY.id}.{self.one.id}"

    async def sign_in(self, user: Any, guilds: Any = None) -> None:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")

    async def test_a_dm_gets_the_campaign(self) -> None:
        await self.sign_in(ALICE_USER)
        got = await self.client.get(f"/me/campaigns/{self.ref}")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(
            got.json(),
            {
                "ref": self.ref,
                "name": "One",
                "serverName": THURSDAY.name,
                "role": "owner",
                "importsOn": True,
                "settings": {
                    "visibility": "peek",
                    "level": "normal",
                    "keepDays": None,
                    "paused": False,
                    "storyMode": "off",
                    "dndbeyondInvite": "",
                    "mainRules": "2024",
                    "fallbackRules": "2014",
                    "gameMasters": 1,
                },
            },
        )

    async def test_someone_else_in_the_server_is_a_player(self) -> None:
        await self.sign_in(CAROL)
        got = await self.client.get(f"/me/campaigns/{self.ref}")
        self.assertEqual((got.status_code, got.json()["role"]), (200, "player"))

    async def test_a_campaign_that_is_not_theirs_to_see_is_not_found(self) -> None:
        await self.sign_in(DAVE, [QUILLON])
        for ref in (self.ref, f"{THURSDAY.id}.nope", "garbage"):
            got = await self.client.get(f"/me/campaigns/{ref}")
            self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}), ref)

    async def test_signed_out_gets_nothing(self) -> None:
        got = await self.client.get(f"/me/campaigns/{self.ref}")
        self.assertEqual(got.status_code, 401)
