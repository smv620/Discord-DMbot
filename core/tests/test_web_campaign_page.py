"""One campaign's own page (#2416): `GET /me/campaigns/{ref}` and the `ref` on `/me`'s campaigns.

A DM of the campaign is its "owner" or a "co-dm"; anyone else in the server is a "player"; a
person outside the server, a campaign that isn't there and another server's campaign all get the
same refusal. Needs the database (skipped without one, like the other web tests)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.web import sessions
from dmbot.web.campaign_page import read_campaign
from dmbot.web.me import build_me
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import QUILLON, THURSDAY

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
