"""Optional rules from other books (#49): the catalog (pure) and /dmbot optionalrules."""

from __future__ import annotations

import unittest
import unittest.mock
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

    def test_each_books_rules_are_listed_together(self) -> None:
        # rules_text gives each book one heading, so a book's rules must sit together.
        books = [r.source for r in optional.CATALOG]
        runs = [b for i, b in enumerate(books) if i == 0 or books[i - 1] != b]
        self.assertEqual(len(runs), len(set(books)))

    def test_which_rules_apply_to_a_ruleset(self) -> None:
        newer = {r.id for r in optional.applying("2024")}
        older = {r.id for r in optional.applying("2014")}
        self.assertIn("xge-no-long-rest", newer)  # the 2024 rules don't cover it
        self.assertNotIn("tce-custom-origin", newer)  # the 2024 rules changed origins
        self.assertIn("tce-custom-origin", older)
        self.assertEqual(older, {r.id for r in optional.CATALOG})
        self.assertEqual(optional.applying("an unknown ruleset"), [])

    def test_unchecked_rules_are_offered_to_2024_too(self) -> None:
        # Nobody has named the 2024 section that covers these; offering does no harm.
        newer = {r.id for r in optional.applying("2024")}
        self.assertIn("xge-identify-spell", newer)
        self.assertIn("xge-falling-rate", newer)

    def test_the_covered_note_follows_the_catalog(self) -> None:
        left_out = optional.covered_by("2024")
        self.assertEqual(
            {r.id for r in left_out},
            {r.id for r in optional.CATALOG} - {r.id for r in optional.applying("2024")},
        )
        for r in left_out:
            self.assertIn(r.name[1:], ui.COVERED_2024)
        self.assertNotIn("Spotting a spell"[1:], ui.COVERED_2024)
        self.assertEqual(ui.covered_note("2014"), "")  # 2014 leaves nothing out

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
        edited: list[tuple[str, Any]] = []

        async def edit_original_response(*, content: str = "", view: Any = None, **_: Any) -> None:
            edited.append((content, view))

        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(side_effect=edit_original_response),
            edited=edited,
        )

    @staticmethod
    def told(it: Any) -> str:
        """The private follow-up sent after the switch answered Discord."""
        return str(it.followup.send.await_args.args[0])

    async def open_list(self, user_id: int = DM) -> Any:
        it = self.it(user_id)
        await ui.dmbot_optional_rules.callback(it)  # type: ignore[call-arg]
        return it

    async def switch(self, menu: Any, rule_id: str, user_id: int = DM) -> Any:
        (value,) = [o.value for o in menu.pick.options if o.value.startswith(f"{rule_id}:")]
        menu.pick._values = [value]  # what Discord sets when one is picked
        it = self.it(user_id)
        await menu._switch(it)
        return it

    async def test_the_dm_sees_the_rules_for_their_main_rules(self) -> None:
        it = await self.open_list()
        text, kw = it.response.sent[0]
        self.assertTrue(kw["ephemeral"])
        self.assertIn("Optional rules: Frostmaiden", text)
        self.assertIn("**Xanathar's Guide to Everything**\n✅ **Going without a long rest**", text)
        self.assertIn("doesn't check rules yet", text)
        self.assertIn("add to your main rules (2024 rules).", text)  # no "(newest)" here
        self.assertNotIn("Customizing your origin", text)  # not for the 2024 rules
        self.assertTrue(text.endswith(ui.DISCLAIMER))  # below the list
        self.assertIn(ui.COVERED_2024, text)
        self.assertNotIn("supplement", text.lower())  # plain words
        labels = [o.label for o in kw["view"].pick.options]
        self.assertNotIn("Customizing your origin", labels)

    async def test_a_player_cannot(self) -> None:
        it = await self.open_list(PLAYER)
        self.assertIn("not the DM of any campaign", it.response.sent[0][0])

    async def test_switching_a_rule_saves_it_and_tells_the_dm_screen(self) -> None:
        it = await self.open_list(MANAGER)  # server managers may too
        menu = it.response.sent[0][1]["view"]
        it = await self.switch(menu, "xge-sleep", MANAGER)
        overrides = await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id)
        self.assertEqual(overrides, {"xge-sleep": False})
        text, view = it.edited[0]
        self.assertTrue(text.startswith("⬜ **Sleeping in armor** is now off.\n📚"))  # news first
        self.assertIn("⬜ **Sleeping in armor**:", text)
        self.assertNotIn(ui.NO_SCREEN_NOTE, text)
        (note,) = self.posted.await_args_list
        self.assertEqual(note.args[0], SCREEN)
        self.assertTrue(
            note.args[1].startswith("⬜ Optional rule turned off: **Sleeping in armor** (by ")
        )
        (option,) = [o for o in view.pick.options if o.value.startswith("xge-sleep:")]
        self.assertEqual(option.description, "⬜ Off now · pick to turn on")
        await self.switch(view, "xge-sleep", MANAGER)  # and back on
        overrides = await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id)
        self.assertEqual(overrides, {"xge-sleep": True})

    async def test_the_switch_answers_discord_before_the_slow_steps(self) -> None:
        menu = (await self.open_list()).response.sent[0][1]["view"]
        it = self.it()
        order: list[str] = []
        it.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))

        async def slow_post(*_: Any) -> bool:
            order.append("post")
            return True

        self.posted.side_effect = slow_post
        (value,) = [o.value for o in menu.pick.options if o.value.startswith("xge-sleep:")]
        menu.pick._values = [value]
        await menu._switch(it)
        self.assertEqual(order, ["defer", "post"])
        self.assertTrue(it.edited[0][0].startswith("⬜ **Sleeping in armor** is now off."))

    async def test_an_old_menu_never_flips_a_rule_the_wrong_way(self) -> None:
        old = (await self.open_list()).response.sent[0][1]["view"]
        newer = (await self.open_list()).response.sent[0][1]["view"]
        await self.switch(newer, "xge-sleep")  # someone else turns it off
        await self.switch(old, "xge-sleep")  # the old menu also said "turn off"
        overrides = await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id)
        self.assertEqual(overrides, {"xge-sleep": False})  # still off, not flipped back on

    async def test_every_switch_checks_who_is_asking(self) -> None:
        menu = (await self.open_list()).response.sent[0][1]["view"]
        await self.campaigns.add_dm(GUILD, self.campaign.id, PLAYER)
        async with self.db.guild(GUILD) as conn:  # handed over first: the owner can't go
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE id = %s", (PLAYER, self.campaign.id)
            )
        await self.campaigns.remove_dm(GUILD, self.campaign.id, DM)  # no longer this DM's
        it = await self.switch(menu, "xge-sleep")
        self.assertIn("Only this campaign's DM", self.told(it))
        self.assertEqual(await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id), {})
        self.posted.assert_not_awaited()

    async def test_a_rule_the_main_rules_no_longer_take_is_refused(self) -> None:
        await self.campaigns.set_rulesets(GUILD, self.campaign.id, "2014", "none")
        menu = (await self.open_list()).response.sent[0][1]["view"]
        await self.campaigns.set_rulesets(GUILD, self.campaign.id, "2024", "2014")
        it = await self.switch(menu, "tce-custom-origin")  # 2014 only
        self.assertIn("isn't in this campaign's list", self.told(it))
        self.assertEqual(await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id), {})

    async def test_without_a_dm_screen_the_choice_is_still_saved(self) -> None:
        await self.campaigns.set_dm_screen(GUILD, self.campaign.id, None)
        menu = (await self.open_list()).response.sent[0][1]["view"]
        await self.switch(menu, "tce-parley")
        self.assertEqual(
            await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id),
            {"tce-parley": False},
        )
        self.posted.assert_not_awaited()

    async def test_a_dm_screen_that_cannot_be_written_to_is_mentioned(self) -> None:
        self.posted.return_value = False  # the channel was deleted
        menu = (await self.open_list()).response.sent[0][1]["view"]
        it = await self.switch(menu, "tce-parley")
        text, _ = it.edited[0]
        self.assertTrue(
            text.startswith(f"⬜ **Talking with monsters** is now off. {ui.NO_SCREEN_NOTE}\n")
        )
        self.assertLessEqual(len(text), ui.TEXT_MAX)

    async def test_a_long_list_is_cut_between_lines_and_keeps_its_last_lines(self) -> None:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        with unittest.mock.patch.object(ui, "TEXT_MAX", 700):
            text = ui.rules_text(campaign, {}, "✅ news")
        self.assertLessEqual(len(text), 700)
        self.assertTrue(text.startswith("✅ news\n"))
        self.assertIn("\n…\n" + ui.COVERED_2024, text)
        self.assertTrue(text.endswith(ui.DISCLAIMER))
        whole = {f"✅ **{r.name}**: {r.summary}" for r in optional.applying("2024")}
        listed = [line for line in text.split("\n")[1:] if line.startswith("✅")]
        self.assertTrue(listed)
        self.assertLessEqual(set(listed), whole)  # no rule cut in half

    async def test_a_menu_that_cannot_be_updated_still_says_what_was_saved(self) -> None:
        menu = (await self.open_list()).response.sent[0][1]["view"]
        (value,) = [o.value for o in menu.pick.options if o.value.startswith("tce-parley:")]
        menu.pick._values = [value]
        it = self.it()
        it.edit_original_response = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404, reason="gone"), "gone")
        )
        await menu._switch(it)
        self.assertEqual(
            await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id),
            {"tce-parley": False},
        )
        self.posted.assert_awaited_once()  # the DM screen was told first
        self.assertEqual(self.told(it), "Saved. ⬜ **Talking with monsters** is now off.")

    async def test_too_late_to_answer_still_saves_quietly(self) -> None:
        self.posted.return_value = False
        menu = (await self.open_list()).response.sent[0][1]["view"]
        (value,) = [o.value for o in menu.pick.options if o.value.startswith("tce-parley:")]
        menu.pick._values = [value]
        it = self.it()
        gone = discord.HTTPException(MagicMock(status=404, reason="gone"), "gone")
        it.edit_original_response = AsyncMock(side_effect=gone)
        it.followup.send = AsyncMock(side_effect=gone)
        await menu._switch(it)  # doesn't raise
        it.followup.send.assert_awaited_once()
        self.assertIn(ui.NO_SCREEN_NOTE, it.followup.send.await_args.args[0])
        self.assertEqual(
            await self.campaigns.optional_rule_overrides(GUILD, self.campaign.id),
            {"tce-parley": False},
        )

    async def test_a_ruleset_with_no_optional_rules_says_so_plainly(self) -> None:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        with unittest.mock.patch.object(optional, "applying", return_value=[]):
            text = ui.rules_text(campaign, {})
        self.assertIn("DMbot has no optional rules to offer for the 2024 rules yet.", text)
        self.assertNotIn("doesn't check rules yet", text)  # nothing to keep on
        self.assertNotIn(ui.COVERED_2024, text)

    async def test_several_campaigns_ask_which_one_first(self) -> None:
        await self.campaigns.create(GUILD, "Strahd", DM)
        it = await self.open_list()
        text, kw = it.response.sent[0]
        self.assertIn("Which campaign's optional rules?", text)
        kw["view"].pick._values = [self.campaign.id]
        it = self.it()
        await kw["view"]._picked(it)
        self.assertIn("Optional rules: Frostmaiden", it.response.sent[0][0])

    async def test_another_servers_campaign_is_refused(self) -> None:
        other = await self.campaigns.create(GUILD + 1, "Elsewhere", DM)
        it = self.it()
        self.assertIsNone(await ui._campaign_for(it, other.id))
        self.assertIn("Only this campaign's DM", it.response.sent[0][0])

    async def test_the_longest_list_fits_one_message(self) -> None:
        long = await self.campaigns.create(
            GUILD, "x" * 80, DM, target_ruleset="2014", fallback_ruleset="none"
        )
        text = ui.rules_text(long, {})
        self.assertLessEqual(len(text), 2000)
        last = optional.applying("2014")[-1]
        self.assertIn(f"**{last.name}**: {last.summary}\n", text)  # nothing cut
        self.assertTrue(text.endswith(ui.DISCLAIMER))

    async def test_a_campaign_with_rules_off_by_default_shows_them_off(self) -> None:
        await self.campaigns.set_optional_rules_default(GUILD, self.campaign.id, False)
        it = await self.open_list()
        self.assertIn("⬜ **Going without a long rest**", it.response.sent[0][0])

    async def test_the_choices_survive_a_backup(self) -> None:
        await self.campaigns.set_optional_rule(GUILD, self.campaign.id, "tce-parley", False)
        data = await self.campaigns.export(GUILD, self.campaign.id)
        restored = await self.campaigns.import_backup(GUILD, data, DM)
        overrides = await self.campaigns.optional_rule_overrides(GUILD, restored.id)
        self.assertEqual(overrides, {"tce-parley": False})
