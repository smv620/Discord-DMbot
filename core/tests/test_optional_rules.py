"""Optional rules from other books (#49): the catalog (pure) and /dmbot optionalrules."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import RULESETS, CampaignStore
from dmbot.campaigns.store import _valid_rule_id
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.rules import optional
from dmbot.sessions import SessionStore
from dmbot.ui import optional_rules as ui
from dmbot.ui.logic import PHONE_LABEL_MAX, SELECT_OPTIONS_MAX
from tests.pg import DatabaseTest
from tests.test_memory_names import FakeResponse

GUILD, DM, PLAYER, MANAGER, SCREEN = 1, 7, 8, 10, 3


class Catalog(unittest.TestCase):
    def test_every_rule_is_complete_and_stored_safely(self) -> None:
        ids = [r.id for r in optional.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        for r in optional.CATALOG:
            with self.subTest(r.id):
                self.assertTrue(_valid_rule_id(r.id))  # what the store accepts
                self.assertTrue(r.source)
                self.assertTrue(0 < len(r.summary) < 100)
                self.assertLessEqual(len(r.name), PHONE_LABEL_MAX)  # a menu choice
                self.assertTrue(r.applies_to)
                self.assertTrue(set(r.applies_to) <= set(RULESETS))
        self.assertLessEqual(len(optional.CATALOG), SELECT_OPTIONS_MAX)  # one menu

    def test_which_rules_apply_to_a_ruleset(self) -> None:
        newer = {r.id for r in optional.applying("2024")}
        older = {r.id for r in optional.applying("2014")}
        self.assertIn("xge-no-long-rest", newer)  # the 2024 rules don't cover it
        self.assertNotIn("tce-custom-origin", newer)  # the 2024 rules changed origins
        self.assertIn("tce-custom-origin", older)
        self.assertEqual(older, {r.id for r in optional.CATALOG})
        self.assertEqual(optional.applying("homebrew"), [])

    def test_a_rule_follows_the_default_until_the_dm_switches_it(self) -> None:
        self.assertTrue(optional.is_on("xge-sleep", {}, True))
        self.assertFalse(optional.is_on("xge-sleep", {}, False))
        self.assertFalse(optional.is_on("xge-sleep", {"xge-sleep": False}, True))
        self.assertTrue(optional.is_on("xge-sleep", {"xge-sleep": True}, False))


class Command(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
        )
        self.posted = AsyncMock(return_value=True)
        self.bot.post = self.posted  # type: ignore[method-assign]
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        await self.campaigns.set_dm_screen(GUILD, self.campaign.id, SCREEN)

    def it(self, user_id: int = DM) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        user.guild_permissions = (
            discord.Permissions(manage_guild=True)
            if user_id == MANAGER
            else discord.Permissions.none()
        )
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_the_dm_sees_the_rules_for_their_main_rules(self) -> None:
        it = self.it()
        await ui.dmbot_optional_rules.callback(it)  # type: ignore[call-arg]
        text, kw = it.response.sent[0]
        self.assertTrue(kw["ephemeral"])
        self.assertIn("Rules from other books: Frostmaiden", text)
        self.assertIn("✅ **Going without a long rest**", text)
        self.assertIn("Xanathar's Guide to Everything", text)
        self.assertNotIn("Customising your origin", text)  # not for the 2024 rules
        self.assertNotIn("supplement", text.lower())  # plain words

    async def test_a_player_cannot(self) -> None:
        it = self.it(PLAYER)
        await ui.dmbot_optional_rules.callback(it)  # type: ignore[call-arg]
        self.assertIn("not the DM of any campaign", it.response.sent[0][0])

    async def test_turning_a_rule_off_saves_it_and_tells_the_dm_screen(self) -> None:
        it = self.it(MANAGER)  # server managers may too
        await ui.dmbot_optional_rules.callback(it)  # type: ignore[call-arg]
        menu = it.response.sent[0][1]["view"]
        menu.pick._values = ["xge-sleep"]  # what Discord sets when one is picked
        it = self.it(MANAGER)
        await menu._toggle(it)
        overrides = await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id)
        self.assertEqual(overrides, {"xge-sleep": False})
        text, view = it.response.edited[0]
        self.assertIn("⬜ **Sleeping in armour**", text)
        self.posted.assert_awaited_once_with(SCREEN, "Optional rule off: Sleeping in armour.")
        (option,) = [o for o in view.pick.options if o.value == "xge-sleep"]
        self.assertEqual(option.description, "Off: tap to turn on")
        view.pick._values = ["xge-sleep"]  # and back on
        it = self.it(MANAGER)
        await view._toggle(it)
        overrides = await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id)
        self.assertEqual(overrides, {"xge-sleep": True})

    async def test_a_campaign_with_rules_off_by_default_shows_them_off(self) -> None:
        await self.campaigns.set_optional_rules_default(GUILD, self.campaign.id, False)
        it = self.it()
        await ui.dmbot_optional_rules.callback(it)  # type: ignore[call-arg]
        self.assertIn("⬜ **Going without a long rest**", it.response.sent[0][0])

    async def test_the_choices_survive_a_backup(self) -> None:
        await self.campaigns.set_optional_rule(GUILD, self.campaign.id, "tce-parley", False)
        data = await self.campaigns.export(GUILD, self.campaign.id)
        restored = await self.campaigns.import_backup(GUILD, data, DM)
        overrides = await self.campaigns.optional_rule_overrides(GUILD, restored.id)
        self.assertEqual(overrides, {"tce-parley": False})
