"""The Account page's Discord accounts (#4473, #2534): the list with an opaque key and a name for
each, adding one by signing in as it on Discord (never a session for the added account), and taking
one off. Needs Postgres; invented numbers and names."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot.campaigns.store import CampaignStore
from dmbot.web import tokens
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.discord_accounts import key_of
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, SECRET, SITE, FakeDiscord, settings

ADDED = 424_242_001
OWNS_CAMPAIGNS = 424_242_002
HOST = "web-api.internal.test"


class DiscordAccounts(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.discord = FakeDiscord()
        app = create_app(settings(), self.db, self.discord, clock=lambda: self.now)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=f"https://{HOST}",
            follow_redirects=False,
        )
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def add(self, as_id: int, *, code: str = "good-code") -> httpx.Response:
        """The whole round: start, Discord answers as `as_id`, come back."""
        start = await self.client.get("/auth/discord/add")
        if not start.headers["location"].startswith("https://discord.com"):
            return start  # refused before Discord
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.discord.user_info = DiscordUser(id=as_id, name="Mira's phone", email=None)
        params = {"state": state}
        if code:
            params["code"] = code
        return await self.client.get("/auth/discord/callback", params=params)

    async def accounts(self) -> list[dict[str, Any]]:
        got: list[dict[str, Any]] = (await self.client.get("/me")).json()["discordAccounts"]
        return got


class TheList(DiscordAccounts):
    async def test_an_account_lists_only_itself_by_name_with_no_number(self) -> None:
        (only,) = await self.accounts()
        self.assertEqual(
            (only["name"], only["current"], only["canRemove"]), (ALICE.name, True, False)
        )
        self.assertNotIn(str(ALICE.id), str(only))

    async def test_an_added_account_shows_its_day_and_can_be_removed_but_not_the_one_in_use(
        self,
    ) -> None:
        done = await self.add(ADDED)
        self.assertEqual(done.headers["location"], f"{SITE}/account?discord=added")
        used, added = await self.accounts()
        day = datetime.fromtimestamp(self.now, UTC)
        self.assertEqual(
            (used["current"], used["canRemove"], added["current"], added["canRemove"]),
            (True, False, False, True),
        )
        self.assertEqual(added["name"], f"Discord account added on {day.day} {day:%B %Y}")
        self.assertNotIn(str(ADDED), str((await self.client.get("/me")).text))  # (never the number)

    async def test_the_added_account_never_gets_a_session(self) -> None:
        await self.add(ADDED)
        self.assertEqual((await self.client.get("/me")).json()["user"]["id"], str(ALICE.id))


class Adding(DiscordAccounts):
    async def test_adding_the_same_account_again_or_the_one_in_use_says_already(self) -> None:
        await self.add(ADDED)
        again = await self.add(ADDED)
        self.assertEqual(again.headers["location"], f"{SITE}/account?discord=already")
        mine = await self.add(ALICE.id)
        self.assertEqual(mine.headers["location"], f"{SITE}/account?discord=already")

    async def test_an_account_that_runs_campaigns_is_refused(self) -> None:
        await CampaignStore(self.db).create(1, "Frostmaiden", OWNS_CAMPAIGNS)
        done = await self.add(OWNS_CAMPAIGNS)
        self.assertEqual(done.headers["location"], f"{SITE}/account?discord=has_campaigns")
        self.assertEqual(len(await self.accounts()), 1)

    async def test_saying_no_on_discord_is_cancelled_and_adds_nothing(self) -> None:
        done = await self.add(ADDED, code="")
        self.assertEqual(done.headers["location"], f"{SITE}/account?discord=cancelled")
        self.assertEqual(len(await self.accounts()), 1)

    async def test_a_state_for_another_account_or_purpose_is_refused(self) -> None:
        await self.client.get("/auth/discord/add")
        forged = tokens.make(SECRET, "discordadd", "424242", now=self.now, seconds=600)
        other = tokens.make(SECRET, "install", str(ALICE.id), now=self.now, seconds=600)
        for state in (forged, other):
            self.client.cookies.set("__Host-dmbot_discord_add", state, domain=HOST)
            done = await self.client.get(
                "/auth/discord/callback", params={"state": state, "code": "good-code"}
            )
            self.assertNotIn("discord=added", done.headers["location"])
        self.assertEqual(len(await self.accounts()), 1)

    async def test_a_state_that_is_not_this_browsers_cookie_is_refused(self) -> None:
        start = await self.client.get("/auth/discord/add")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.client.cookies.set("__Host-dmbot_discord_add", "other", domain=HOST)
        done = await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )
        self.assertEqual(done.headers["location"], f"{SITE}/account?discord=failed")

    async def test_starting_signed_out_comes_back_to_the_account_page(self) -> None:
        self.client.cookies.delete("__Host-dmbot_session")
        start = await self.client.get("/auth/discord/add")
        self.assertEqual(start.headers["location"], f"{SITE}/account?discord=signed_out")

    async def test_a_state_that_was_already_used_is_refused(self) -> None:
        start = await self.client.get("/auth/discord/add")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.discord.user_info = DiscordUser(id=ADDED, name="Mira's phone", email=None)
        first = await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )
        self.assertEqual(first.headers["location"], f"{SITE}/account?discord=added")
        self.client.cookies.delete("__Host-dmbot_discord_add")  # (the cookie was cleared)
        again = await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )
        self.assertEqual(again.headers["location"], f"{SITE}/account?discord=failed")

    async def test_the_discord_token_is_given_back_even_when_the_add_is_refused(self) -> None:
        await CampaignStore(self.db).create(1, "Frostmaiden", OWNS_CAMPAIGNS)
        before = len(self.discord.revoked)
        await self.add(OWNS_CAMPAIGNS)
        self.assertEqual(len(self.discord.revoked), before + 1)

    async def test_an_expired_state_is_refused(self) -> None:
        start = await self.client.get("/auth/discord/add")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.now += 3600
        done = await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )
        self.assertNotIn("discord=added", done.headers["location"])


class Removing(DiscordAccounts):
    async def test_an_added_account_comes_off_and_the_one_in_use_never_does(self) -> None:
        await self.add(ADDED)
        used, added = await self.accounts()
        headers = {"X-DMbot-Request": "1"}
        refused = await self.client.post(
            f"/me/discord-accounts/{used['key']}/remove", headers=headers
        )
        self.assertEqual((refused.status_code, refused.json()["error"]), (409, "account_in_use"))
        done = await self.client.post(
            f"/me/discord-accounts/{added['key']}/remove", headers=headers
        )
        self.assertEqual(done.status_code, 204)
        (left,) = await self.accounts()
        self.assertEqual(left["key"], used["key"])

    async def test_a_key_that_is_not_this_accounts_is_not_found(self) -> None:
        headers = {"X-DMbot-Request": "1"}
        for key in ("x" * 32, key_of(SECRET, 1234, ADDED)):
            done = await self.client.post(f"/me/discord-accounts/{key}/remove", headers=headers)
            self.assertEqual(done.status_code, 404, key)

    async def test_keys_are_not_the_number_and_differ_by_account(self) -> None:
        self.assertNotIn(str(ADDED), key_of(SECRET, ALICE.id, ADDED))
        self.assertNotEqual(key_of(SECRET, 1, ADDED), key_of(SECRET, 2, ADDED))
