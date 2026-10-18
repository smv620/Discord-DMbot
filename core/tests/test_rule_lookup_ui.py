"""Looking up a rule (#908): `/dmbot rule`, the 📖 button on the Settings card and its form.
Only a campaign's DMs, the answer private, the campaign's own house rules and no one else's,
and no match offers names without picking one."""

from __future__ import annotations

import asyncio
import time
import unittest
import unittest.mock
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign
from dmbot.dm_screen.settings import RuleLookupButton, settings_view
from dmbot.rules import index
from dmbot.rules.homebrew import FULL, SAME_NAME, Homebrew, HomebrewError
from dmbot.rules.house import HouseRule
from dmbot.ui import rule_card
from dmbot.ui import rule_lookup as ui
from dmbot.ui.dmbot_commands import _answer_first as answer_first
from tests.test_memory_names import FakeResponse

GUILD, DM, OTHER_DM, PLAYER = 1, 7, 9, 8
C1, C2 = "a1" * 16, "b2" * 16  # a campaign's id is 32 hex characters
NOW = 1_700_000_000


def campaign(
    cid: str = C1,
    name: str = "Frostmaiden",
    dms: frozenset[int] = frozenset({DM}),
    target: str = "2024",
    fallback: str = "2014",
) -> Campaign:
    return Campaign(
        cid, GUILD, name, NOW - 86400, NOW - 3600, target, fallback, True, dms, None, 50, "peek"
    )


def house(n: int, text: str, cid: str) -> HouseRule:
    return HouseRule(n, cid, text, None, None, None, DM, NOW, NOW)


class FakeStore:
    """Like `HouseRuleStore.list`: the rules of the campaign asked for, and no others."""

    def __init__(self) -> None:
        self.rules: dict[str, list[HouseRule]] = {}
        self.asked: list[str] = []

    async def list(self, guild_id: int, cid: str) -> list[HouseRule]:
        self.asked.append(cid)
        return list(self.rules.get(cid, []))


class FakeBrew:
    """Like `HomebrewStore.list`: the campaign's own entries, and no one else's."""

    def __init__(self) -> None:
        self.entries: dict[str, list[Homebrew]] = {}
        self.asked: list[str] = []

    async def find(
        self, guild_id: int, cid: str, name: str, *, usable_only: bool = False
    ) -> Homebrew | None:
        self.asked.append(cid)
        key = " ".join(name.casefold().split())
        return next(
            (
                e
                for e in self.entries.get(cid, [])
                if " ".join(e.name.casefold().split()) == key and (e.enabled or not usable_only)
            ),
            None,
        )


def brew(cid: str, name: str, text: str, *, kind: str = "monster") -> Homebrew:
    return Homebrew(1, cid, kind, name, text, None, DM, NOW, NOW)


class LookupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        ui._last_lookup.clear()  # (one lookup a person every 3 seconds is module state)
        self.campaigns = {C1: campaign()}
        self.store = FakeStore()
        self.brew = FakeBrew()
        self.playing: str | None = C1
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(
                get=AsyncMock(side_effect=lambda g, cid: self.campaigns.get(cid)),
                list_campaigns=AsyncMock(side_effect=lambda g: list(self.campaigns.values())),
            ),
            house_rules=self.store,
            homebrew=self.brew,
            homebrew_reader=self.brew,
            active_campaign_id=lambda guild_id: self.playing,
            rule_sets=None,
        )
        # The real reader, so a store that can't be read is handled as it is in the bot.
        reader: Any = DMBot.all_house_rules  # a plain function: self can be any stand-in
        self.bot.all_house_rules = lambda g, c: reader(self.bot, g, c)

    def it(self, user_id: int = DM, kind: discord.InteractionType | None = None) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            type=kind or discord.InteractionType.component,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def command(self, name: str, user_id: int = DM) -> Any:
        it = self.it(user_id, discord.InteractionType.application_command)
        await ui.dmbot_rule.callback(it, name)  # type: ignore[call-arg,arg-type]
        return it

    @staticmethod
    def sent(it: Any, index: int = -1) -> tuple[str, Any, dict[str, Any]]:
        call = it.followup.send.await_args_list[index]
        return str(call.args[0]), call.kwargs.get("view"), call.kwargs

    @staticmethod
    def labels(view: Any) -> list[str]:
        return [str(c.label) for c in view.children if isinstance(c, discord.ui.Button)]

    @staticmethod
    def press(view: Any, label: str) -> Any:
        return next(c for c in view.children if getattr(c, "label", None) == label)


class Command(LookupTest):
    async def test_the_dm_gets_the_card_privately(self) -> None:
        it = await self.command("fireball")
        text, view, kwargs = self.sent(it)
        self.assertIn("📖 **Fireball** (spell)", text)
        self.assertIn("Source: SRD 5.2.1, Spell Descriptions", text)
        self.assertTrue(kwargs["ephemeral"])  # never in the channel
        self.assertIsNone(view)  # it fits: no "Read the rest"

    async def test_a_player_gets_the_card_privately_with_the_closing_line(self) -> None:
        it = await self.command("fireball", PLAYER)
        text, _, kwargs = self.sent(it)
        self.assertIn("📖 **Fireball** (spell)", text)
        self.assertTrue(kwargs["ephemeral"])  # never in the channel
        self.assertTrue(text.endswith(rule_card.PLAYER_LINE))
        self.assertEqual(rule_card.PLAYER_LINE, "Your DM decides what applies at your table.")
        dm_text = self.sent(await self.command("fireball", DM))[0]
        self.assertNotIn(rule_card.PLAYER_LINE, dm_text)  # for a player only

    async def test_a_player_in_another_server_is_refused(self) -> None:
        it = self.it(PLAYER, kind=discord.InteractionType.application_command)
        it.guild_id = 999  # not the campaign's server
        await ui.look_up(it, C1, "fireball", guild_id=GUILD)
        self.assertEqual(self.sent(it)[0], ui.NOT_HERE)

    async def test_a_player_looking_up_again_at_once_is_told_to_wait(self) -> None:
        first = await self.command("fireball", PLAYER)
        self.assertIn("Fireball", self.sent(first)[0])
        second = await self.command("fireball", PLAYER)
        self.assertEqual(self.sent(second)[0], ui.ONE_AT_A_TIME)
        real = time.monotonic()
        with unittest.mock.patch("dmbot.ui.rule_lookup.time.monotonic", lambda: real + 4):
            third = await self.command("fireball", PLAYER)
        self.assertIn("Fireball", self.sent(third)[0])
        # A DM is never slowed, and one player's pace is not another's.
        await self.command("fireball", DM)
        again = await self.command("fireball", DM)
        self.assertIn("Fireball", self.sent(again)[0])
        other = await self.command("fireball", OTHER_DM)
        self.assertIn("Fireball", self.sent(other)[0])

    async def test_a_players_no_match_has_no_closing_line_and_no_dm_tags(self) -> None:
        text = self.sent(await self.command("Xyzzyplugh", PLAYER))[0]
        self.assertNotIn(rule_card.PLAYER_LINE, text)
        self.assertIn("ask your DM", text)
        self.assertNotIn("your own books", text)

    async def test_a_players_free_rules_card_has_no_you_decide_line(self) -> None:
        player = self.sent(await self.command("fireball", PLAYER))[0]
        dm = self.sent(await self.command("fireball", DM))[0]
        self.assertNotIn("you decide", player)
        self.assertIn("you decide", dm)

    async def test_a_players_homebrew_card_has_the_same_words_as_the_dms(self) -> None:
        self.brew.entries[C1] = [brew(C1, "Hollow King", "A crowned skeleton.")]
        player = self.sent(await self.command("Hollow King", PLAYER))[0]
        dm = self.sent(await self.command("Hollow King", DM))[0]
        self.assertIn("A crowned skeleton.", player)
        self.assertIn("A crowned skeleton.", dm)
        self.assertNotIn("hidden", player + dm)  # (nothing is hidden from players any more)

    async def test_a_dm_of_one_campaign_who_asks_gets_their_own_view(self) -> None:
        # Runs C2 while C1 (someone else's) is the one being played: C2 is the one they mean.
        self.campaigns[C2] = campaign(C2, "Mine", frozenset({OTHER_DM}))
        self.brew.entries[C2] = [brew(C2, "Hollow King", "Mine only.")]
        text = self.sent(await self.command("Hollow King", OTHER_DM))[0]
        self.assertIn("Mine only.", text)

    async def test_a_player_in_a_server_with_two_campaigns_picks_one(self) -> None:
        self.campaigns[C2] = campaign(C2, "Other", frozenset({OTHER_DM}))
        self.playing = None
        it = await self.command("fireball", PLAYER)
        text, view, _ = self.sent(it)
        self.assertEqual(text, ui.WHICH_CAMPAIGN)
        self.assertIsInstance(view, ui.CampaignChoice)

    async def test_a_close_name_press_is_not_paced_again(self) -> None:
        first = await self.command("Firball", PLAYER)
        view = self.sent(first)[1]
        pressed = self.it(PLAYER)
        await self.press(view, "Fireball").callback(pressed)  # straight after: not "try again"
        self.assertIn("Fireball", self.sent(pressed)[0])

    async def test_each_server_has_its_own_pace(self) -> None:
        self.assertFalse(ui._too_soon(1, PLAYER))
        self.assertTrue(ui._too_soon(1, PLAYER))
        self.assertFalse(ui._too_soon(2, PLAYER))  # another server's table
        self.assertFalse(ui._too_soon(1, OTHER_DM))  # another person

    def test_the_closing_line_still_fits_when_the_last_part_is_full(self) -> None:
        full = ["x" * 1990]
        self.assertEqual(rule_card.for_player(full), [full[0], rule_card.PLAYER_LINE])
        self.assertEqual(rule_card.for_player(["a", "b"]), ["a", f"b\n{rule_card.PLAYER_LINE}"])

    async def test_house_rules_come_first_for_a_player_too(self) -> None:
        self.store.rules[C1] = [house(12, "Fireball needs a free hand.", C1)]
        for who in (DM, PLAYER):
            ui._last_lookup.clear()
            text = self.sent(await self.command("fireball", who))[0]
            self.assertIn("House rule 12", text)
            self.assertIn("Fireball needs a free hand.", text)

    async def test_a_server_with_no_campaign_says_how_to_start_one(self) -> None:
        self.campaigns.clear()
        it = await self.command("fireball")
        self.assertEqual(self.sent(it)[0], ui.NO_CAMPAIGNS)

    async def test_outside_a_server_it_says_to_use_it_in_one(self) -> None:
        it = self.it(kind=discord.InteractionType.application_command)
        it.guild = None
        await ui.dmbot_rule.callback(it, "fireball")  # type: ignore[call-arg,arg-type]
        self.assertIn("Use this in a server", str(it.response.sent[0][0]))

    async def test_the_campaigns_rulesets_decide(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        it = await self.command("Goblin")
        text = self.sent(it)[0]
        self.assertIn("📖 **Goblin** (creature)", text)  # the 2014 creature, not the Warrior
        self.assertIn("[Legacy 2014]", text)
        self.campaigns[C1] = campaign(target="2024", fallback="none")
        it = await self.command("Orc")
        self.assertIn("couldn't find **Orc**", self.sent(it)[0])  # no fallback to read

    async def test_an_older_name_says_what_it_is_now(self) -> None:
        it = await self.command("Goblin")
        text = self.sent(it)[0]
        self.assertIn("**Goblin Warrior**", text)
        self.assertIn("Goblin is now called Goblin Warrior", text)

    async def test_two_campaigns_and_none_playing_asks_which(self) -> None:
        self.campaigns[C2] = campaign(C2, "Other", frozenset({DM, OTHER_DM}))
        self.playing = None
        it = await self.command("fireball")
        text, view, _ = self.sent(it)
        self.assertEqual(text, ui.WHICH_CAMPAIGN)
        view.pick._values = [C2]
        picked = self.it()
        await view.pick.callback(picked)
        self.assertIn("📖 **Fireball**", self.sent(picked)[0])
        self.assertEqual(self.store.asked, [C2])

    async def test_the_playing_campaign_is_used_when_the_dm_has_two(self) -> None:
        self.campaigns[C2] = campaign(C2, "Other")
        self.playing = C2
        await self.command("fireball")
        self.assertEqual(self.store.asked, [C2])

    async def test_a_dm_of_one_of_two_campaigns_uses_that_one(self) -> None:
        self.campaigns[C2] = campaign(C2, "Other", frozenset({OTHER_DM}))
        self.playing = C2  # someone else's is being played
        await self.command("fireball")
        self.assertEqual(self.store.asked, [C1])


class HouseRules(LookupTest):
    async def test_the_campaigns_own_rule_comes_first(self) -> None:
        self.store.rules[C1] = [house(12, "Fireball also burns scrolls", C1)]
        it = await self.command("fireball")
        text = self.sent(it)[0]
        self.assertTrue(text.startswith("🏠 **House rule 12:** Fireball also burns scrolls"))
        self.assertLess(text.index("🏠"), text.index("📖"))

    async def test_another_campaigns_rules_are_never_shown(self) -> None:
        self.campaigns[C2] = campaign(C2, "Other", frozenset({DM}))
        self.store.rules[C2] = [house(1, "Fireball is banned here", C2)]
        self.store.rules[C1] = [house(2, "Nothing about that", C1)]
        it = await self.command("fireball")  # c1 is being played
        text = self.sent(it)[0]
        self.assertNotIn("banned", text)
        self.assertNotIn("🏠", text)
        self.assertEqual(self.store.asked, [C1])

    async def test_a_store_that_cant_be_read_still_gives_the_card(self) -> None:
        self.bot.house_rules = SimpleNamespace(list=AsyncMock(side_effect=RuntimeError("down")))
        it = await self.command("fireball")
        self.assertIn("📖 **Fireball**", self.sent(it)[0])
        self.bot.house_rules = None
        it = await self.command("fireball")
        self.assertIn("📖 **Fireball**", self.sent(it)[0])


class NoMatch(LookupTest):
    async def test_it_says_so_and_offers_close_names_to_press(self) -> None:
        it = await self.command("Firball")
        text, view, _ = self.sent(it)
        self.assertIn("couldn't find **Firball** in the free rules (SRD)", text)
        self.assertIn("Only the free rules are in DMbot so far", text)
        self.assertIn("Did you mean", text)
        self.assertNotIn("📖", text)  # nothing was picked for the DM
        self.assertIn("Fireball", self.labels(view))
        self.assertLessEqual(len(view.children), 5)

    async def test_pressing_a_name_reads_it(self) -> None:
        it = await self.command("Firball")
        view = self.sent(it)[1]
        pressed = self.it()
        await self.press(view, "Fireball").callback(pressed)
        self.assertIn("📖 **Fireball** (spell)", self.sent(pressed)[0])

    async def test_a_player_pressing_a_name_button_reads_it_as_a_player(self) -> None:
        it = await self.command("Firball", PLAYER)
        view = self.sent(it)[1]
        pressed = self.it(PLAYER)
        ui._last_lookup.clear()
        await self.press(view, "Fireball").callback(pressed)
        text = self.sent(pressed)[0]
        self.assertIn("📖 **Fireball** (spell)", text)
        self.assertTrue(text.endswith(rule_card.PLAYER_LINE))

    async def test_nothing_close_gives_plain_advice(self) -> None:
        it = await self.command("Xyzzyplugh")
        text, view, _ = self.sent(it)
        self.assertIn("Check the spelling", text)
        self.assertIsNone(view)

    async def test_a_house_rule_that_names_it_still_shows(self) -> None:
        self.store.rules[C1] = [house(3, "The Frobnicate spell is ours", C1)]
        it = await self.command("Frobnicate")
        self.assertTrue(self.sent(it)[0].startswith("🏠 **House rule 3:**"))


class ReadTheRest(LookupTest):
    async def test_long_text_comes_in_parts_each_sent_privately(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        it = await self.command("Vampire")
        first, view, _ = self.sent(it)
        self.assertIn("📖 **Vampire** (creature)", first)
        self.assertEqual(len(self.labels(view)), 1)
        self.assertRegex(self.labels(view)[0], rf"^{ui.READ_REST_LABEL} \(2 of \d+\)$")
        texts = [first]
        while view is not None:
            pressed = self.it()
            await view.children[0].callback(pressed)
            text, view, kwargs = self.sent(pressed)
            self.assertTrue(kwargs["ephemeral"])
            texts.append(text)
        self.assertGreater(len(texts), 2)
        self.assertTrue(all(len(t) <= 2000 for t in texts))
        self.assertIn(f"(part {len(texts)} of {len(texts)})", texts[-1])


class Form(LookupTest):
    async def submit(self, form: ui.LookupForm, value: str, user_id: int = DM) -> Any:
        form.name._value = value
        it = self.it(user_id, discord.InteractionType.modal_submit)
        await form.on_submit(it)
        return it

    async def test_the_form_has_one_box_a_phone_can_show(self) -> None:
        form = ui.LookupForm(self.campaigns[C1])
        self.assertEqual(len(form.children), 1)
        self.assertEqual(form.name.label, "Spell, condition, creature or rule")
        self.assertLessEqual(len(str(form.name.label)), 45)
        self.assertLessEqual(len(ui.FORM_TITLE), 45)

    async def test_it_gives_the_card(self) -> None:
        it = await self.submit(ui.LookupForm(self.campaigns[C1]), "  grappled ")
        self.assertIn("📖 **Grappled** (condition)", self.sent(it)[0])

    async def test_an_empty_box_says_what_to_do(self) -> None:
        it = await self.submit(ui.LookupForm(self.campaigns[C1]), "   ")
        self.assertEqual(self.sent(it)[0], ui.EMPTY)

    async def test_someone_who_stopped_being_a_dm_meanwhile_reads_as_a_player(self) -> None:
        form = ui.LookupForm(self.campaigns[C1])
        self.campaigns[C1] = campaign(dms=frozenset({OTHER_DM}))
        it = await self.submit(form, "fireball")
        self.assertTrue(self.sent(it)[0].endswith(rule_card.PLAYER_LINE))

    async def test_a_campaign_deleted_meanwhile_is_said(self) -> None:
        form = ui.LookupForm(self.campaigns[C1])
        self.campaigns.clear()
        it = await self.submit(form, "fireball")
        self.assertEqual(self.sent(it)[0], ui.CAMPAIGN_GONE)


class SettingsButton(LookupTest):
    async def press_button(self, user_id: int) -> Any:
        it = self.it(user_id)
        it.response.send_message = AsyncMock()
        it.response.send_modal = AsyncMock()
        await RuleLookupButton(C1).callback(it)
        return it

    async def test_the_dm_gets_the_form(self) -> None:
        it = await self.press_button(DM)
        form = it.response.send_modal.await_args.args[0]
        self.assertIsInstance(form, ui.LookupForm)
        self.assertEqual(form.campaign.id, C1)

    async def test_a_player_or_another_campaigns_dm_is_refused(self) -> None:
        for who in (PLAYER, OTHER_DM):
            it = await self.press_button(who)
            it.response.send_modal.assert_not_awaited()
            self.assertEqual(it.response.send_message.await_args.args[0], ui.ONLY_DMS)

    async def test_the_button_is_only_on_a_dms_card(self) -> None:
        c = campaign()
        for viewer, expected in ((DM, True), (PLAYER, False)):
            view = settings_view(c, None, viewer)
            items: list[Any] = [getattr(i, "item", i) for i in view.children]
            ids = [str(i.custom_id) for i in items]
            self.assertEqual(f"dmbot:rulelookup:{C1}" in ids, expected, viewer)

    async def test_the_button_survives_a_restart_and_fits_a_phone(self) -> None:
        button = RuleLookupButton("a" * 32)
        template = RuleLookupButton.__discord_ui_compiled_template__
        self.assertIsNotNone(template.fullmatch(str(button.item.custom_id)))
        self.assertLessEqual(len(str(button.item.label)), 25)
        self.assertEqual(button.item.label, "Look up a rule")


class Typeahead(LookupTest):
    async def test_a_database_that_fails_or_hangs_gives_the_default_order(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        it = self.it(kind=discord.InteractionType.autocomplete)
        self.bot.campaigns.get = AsyncMock(side_effect=RuntimeError("down"))
        values = [c.value for c in await ui._typeahead(it, "goblin")]
        self.assertEqual(values[0], "Goblin Warrior")

        async def hang(*_: Any) -> None:
            await asyncio.sleep(60)

        self.bot.campaigns.get = hang
        with unittest.mock.patch.object(ui, "TYPEAHEAD_WAIT_S", 0.05):
            values = [c.value for c in await ui._typeahead(it, "goblin")]
        self.assertEqual(values[0], "Goblin Warrior")

    async def test_outside_a_server_and_with_nothing_playing_it_still_answers(self) -> None:
        it = self.it(kind=discord.InteractionType.autocomplete)
        it.guild = None
        self.assertTrue(await ui._typeahead(it, "fire"))
        it = self.it(kind=discord.InteractionType.autocomplete)
        self.playing = None
        self.assertTrue(await ui._typeahead(it, "fire"))

    async def test_odd_input_is_safe_and_quick(self) -> None:
        it = self.it(kind=discord.InteractionType.autocomplete)
        for typed in ("x" * 300, "  ", "!!!", "'", "(((", "fire" * 40):
            await ui._typeahead(it, typed)  # no error
        started = time.perf_counter()
        for typed in ("", "f", "fire", "goblin", "zzz"):
            await ui._typeahead(it, typed)
        self.assertLess(time.perf_counter() - started, 2.0)  # 5 keystrokes, with room to spare

    async def test_names_that_begin_as_typed_at_most_25_each_marked(self) -> None:
        it = self.it(kind=discord.InteractionType.autocomplete)
        choices = await ui._typeahead(it, "fire")
        self.assertTrue(choices)
        self.assertLessEqual(len(choices), 25)
        self.assertTrue(choices[0].value.lower().startswith("fire"))
        self.assertTrue(all(len(c.name) <= 100 and len(c.value) <= 100 for c in choices))
        self.assertIn("Fireball (spell)", [c.name for c in choices])
        self.assertEqual(len(await ui._typeahead(it, "")), 25)

    async def test_the_playing_campaigns_rulesets_come_first(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        it = self.it(kind=discord.InteractionType.autocomplete)
        values = [c.value for c in await ui._typeahead(it, "goblin")]
        self.assertEqual(values[0], "Goblin")

    async def test_a_player_gets_the_names_in_the_campaigns_order_and_no_homebrew(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        self.brew.entries[C1] = [brew(C1, "Goblinoid Secret", "Hidden.")]
        it = self.it(PLAYER, kind=discord.InteractionType.autocomplete)
        values = [c.value for c in await ui._typeahead(it, "goblin")]
        self.assertEqual(values[0], "Goblin")
        self.assertNotIn("Goblinoid Secret", values)  # the list holds the free rules only


class Timeout(LookupTest):
    async def test_a_timed_out_card_keeps_its_words_and_loses_only_its_buttons(self) -> None:
        for view in (ui.Card(["one", "two"], 1), ui.Suggestions(campaign(), [])):
            origin = self.it()
            view.origin = origin
            await view.on_timeout()
            call = origin.edit_original_response.await_args
            self.assertEqual(call.kwargs, {"view": None})  # no new content: the text stays

    async def test_a_timeout_with_the_message_gone_is_quiet(self) -> None:
        view = ui.Card(["one", "two"], 1)
        origin = self.it()
        origin.edit_original_response = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "x")
        )
        view.origin = origin
        await view.on_timeout()


class SuggestionButtons(LookupTest):
    async def test_an_older_only_name_says_so(self) -> None:
        near = index.srd().suggest("Orcc", "2024", "2014")
        labels = self.labels(ui.Suggestions(campaign(), near))
        self.assertIn("Orc [Legacy 2014]", labels)
        self.assertTrue(all(len(label) <= 80 for label in labels))
        long = index.Entry("monster", "N" * 90, "2014", "SRD 5.1", "x", 1, "t", {})
        (label,) = self.labels(ui.Suggestions(campaign(), [long]))
        self.assertLessEqual(len(label), 80)
        self.assertTrue(label.endswith("[Legacy 2014]"))

    async def test_pressing_a_name_reads_that_kind_of_thing(self) -> None:
        spell = index.Entry("spell", "Sleep", "2024", "SRD 5.2.1", "Spell Descriptions", 1, "t", {})
        thing = index.Entry("condition", "Sleep", "2024", "SRD 5.2.1", "x", 2, "t", {})
        view = ui.Suggestions(self.campaigns[C1], [spell, thing])
        read = self.it()
        await self.press(view, "Sleep (spell)").callback(read)
        self.assertIn("📖 **Sleep** (spell)", self.sent(read)[0])
        other = self.it()  # there is no condition Sleep: the spell is not read in its place
        await self.press(view, "Sleep (condition)").callback(other)
        self.assertIn("couldn't find **Sleep**", self.sent(other)[0])

    async def test_the_same_name_of_two_kinds_is_two_buttons_that_say_which(self) -> None:
        def entry(kind: str) -> index.Entry:
            return index.Entry(kind, "Sleep", "2024", "SRD 5.2.1", "x", 1, "text", {})

        view = ui.Suggestions(campaign(), [entry("spell"), entry("condition"), entry("spell")])
        self.assertEqual(self.labels(view), ["Sleep (spell)", "Sleep (condition)"])

    async def test_a_name_that_is_only_one_thing_is_bare(self) -> None:
        near = index.srd().suggest("Firball", "2024", "2014")
        self.assertIn("Fireball", self.labels(ui.Suggestions(campaign(), near)))


class ReadTheRestParts(LookupTest):
    async def test_the_pressed_message_loses_its_button_so_a_part_is_never_sent_twice(self) -> None:
        view = ui.Card(["first", "second", "third"], 1)
        pressed = self.it()
        await view.children[0].callback(pressed)
        self.assertEqual(pressed.response.edited, [("", None)])  # its own buttons are gone
        self.assertEqual(self.sent(pressed)[0], "second")  # and the next part is a new message

    async def test_the_last_part_has_no_button_and_each_press_sends_one_part(self) -> None:
        parts = ["first", "second", "third", "fourth"]
        view: Any = ui.Card(parts, 1)
        seen = []
        while view is not None:
            pressed = self.it()
            await view.children[0].callback(pressed)
            text, view, _ = self.sent(pressed)
            seen.append(text)
        self.assertEqual(seen, ["second", "third", "fourth"])


class Order(LookupTest):
    async def test_discord_is_answered_before_the_slow_steps(self) -> None:
        calls: list[str] = []

        async def answer(interaction: Any, **kw: Any) -> None:
            calls.append("answered")
            await answer_first(interaction, **kw)

        async def slow_get(guild_id: int, cid: str) -> Campaign:
            calls.append("database")
            return self.campaigns[cid]

        self.bot.campaigns.get = slow_get
        with unittest.mock.patch("dmbot.ui.rule_lookup._answer_first", answer):
            await ui.look_up(self.it(), C1, "fireball", guild_id=GUILD)
        self.assertEqual(calls[:2], ["answered", "database"])


class Words(unittest.TestCase):
    def test_the_words_are_plain_and_name_the_free_rules(self) -> None:
        for text in (
            ui.ONLY_DMS,
            ui.NOT_HERE,
            ui.ONE_AT_A_TIME,
            ui.EMPTY,
            ui.TOO_LONG,
            ui.WHICH_CAMPAIGN,
        ):
            self.assertNotRegex(text, r"(?i)\b(srd|ruleset|index|query|database)\b")
        self.assertIn("free rules (SRD)", rule_card.NOT_A_RULING)
        self.assertEqual(rule_card.FREE_RULES, "the free rules (SRD)")


if __name__ == "__main__":
    unittest.main()


def from_pack(cid: str, name: str, text: str) -> Homebrew:
    """As the reader makes a followed pack's entry: no id of its own, a source."""
    return Homebrew(
        -1001, cid, "item", name, text, "A short version", 0, NOW, NOW, 2,
        source="Frost Pack homebrew, v2",
    )  # fmt: skip


class MakeItMine(LookupTest):
    """✏️ Make it my own on a followed pack's entry (#1541)."""

    async def card(self, name: str = "Ember Lamp") -> Any:
        it = await self.command(name)
        return self.sent(it, 0)[1]

    async def test_the_button_is_on_a_pack_entry_and_not_on_an_own_one(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        view = await self.card()
        self.assertEqual(self.labels(view), [ui.MAKE_MINE_LABEL])
        self.brew.entries[C1] = [brew(C1, "Ember Lamp", "Mine.")]
        it = await self.command("Ember Lamp")
        self.assertIsNone(self.sent(it, 0)[1])  # an own entry has nothing to copy

    async def test_a_player_never_gets_a_card_so_never_the_button(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        it = await self.command("Ember Lamp", user_id=PLAYER)
        self.assertNotIn(ui.MAKE_MINE_LABEL, str(it.followup.send.await_args_list))

    async def test_the_copy_is_the_pack_entry_as_it_is_then_edit_opens(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        view = await self.card()
        saved = brew(C1, "Ember Lamp", "Glows warmly.", kind="item")
        self.brew.add = AsyncMock(return_value=saved)  # type: ignore[attr-defined]
        self.bot.homebrew_changed = MagicMock()
        it = self.it()
        await self.press(view, ui.MAKE_MINE_LABEL).callback(it)
        add = self.brew.add  # type: ignore[attr-defined]
        add.assert_awaited_once()
        self.assertEqual(add.await_args.args, (GUILD, C1, DM))
        self.assertEqual(
            add.await_args.kwargs,
            {"kind": "item", "name": "Ember Lamp", "text": "Glows warmly.",
             "summary": "A short version"},
        )  # fmt: skip
        self.assertNotIn("from_book", add.await_args.kwargs)  # never a book's
        self.bot.homebrew_changed.assert_called_once_with(GUILD, C1)
        self.assertEqual(type(it.response.modal).__name__, "MakeMineForm")

    async def test_only_a_dm_can_press_it_and_nothing_is_copied_otherwise(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        view = await self.card()
        self.brew.add = AsyncMock()  # type: ignore[attr-defined]
        it = self.it(user_id=PLAYER)
        await self.press(view, ui.MAKE_MINE_LABEL).callback(it)
        self.brew.add.assert_not_called()  # type: ignore[attr-defined]
        self.assertIsNone(it.response.modal)
        self.assertEqual(it.response.sent[0][0], ui.ONLY_DMS)

    async def test_a_full_homebrew_or_a_name_clash_copies_nothing_and_says_so(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        view = await self.card()
        self.brew.add = AsyncMock(side_effect=HomebrewError("Full."))  # type: ignore[attr-defined]
        it = self.it()
        await self.press(view, ui.MAKE_MINE_LABEL).callback(it)
        self.assertIsNone(it.response.modal)
        self.assertEqual(it.response.sent[0][0], "Full.")  # an unknown refusal passes through
        for refusal, words in ((SAME_NAME, ui.ALREADY_MINE), (FULL, ui.FULL_COPY)):
            self.brew.add = AsyncMock(side_effect=HomebrewError(refusal))  # type: ignore[attr-defined]
            again = self.it()
            await self.press(view, ui.MAKE_MINE_LABEL).callback(again)
            self.assertEqual(again.response.sent[0][0], words)

    async def test_a_long_pack_entry_keeps_the_button_when_the_rest_is_read(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly. " * 400)]
        view = await self.card()
        self.assertEqual(len(self.labels(view)), 2)  # Read the rest + Make it my own
        it = self.it()
        await self.press(view, f"{ui.READ_REST_LABEL} (2 of {len(view.parts)})").callback(it)
        kept = it.response.edited[0][1]
        self.assertEqual(self.labels(kept), [ui.MAKE_MINE_LABEL])

    async def test_an_unexpected_failure_is_said_and_logged(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        view = await self.card()
        self.brew.add = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[attr-defined]
        it = self.it()
        with self.assertLogs("dmbot.ui.rule_lookup", level="ERROR"):
            await self.press(view, ui.MAKE_MINE_LABEL).callback(it)
        self.assertEqual(it.response.sent[0][0], ui.COULDNT_COPY)

    async def test_the_button_comes_off_the_card_once_the_copy_exists(self) -> None:
        self.brew.entries[C1] = [from_pack(C1, "Ember Lamp", "Glows warmly.")]
        view = await self.card()
        self.brew.add = AsyncMock(return_value=brew(C1, "Ember Lamp", "x"))  # type: ignore[attr-defined]
        it = self.it()
        await self.press(view, ui.MAKE_MINE_LABEL).callback(it)
        it.edit_original_response.assert_awaited_once_with(view=None)

    async def test_saving_the_form_shows_the_entry_as_the_campaigns_own(self) -> None:
        from dmbot.ui.homebrew import MINE_NOTE, EntryMenu, MakeMineForm

        saved = brew(C1, "Ember Lamp", "Glows warmly.", kind="item")
        form = MakeMineForm(campaign(), saved, 0)
        form.saved = saved
        it = self.it(kind=discord.InteractionType.modal_submit)
        with unittest.mock.patch("dmbot.ui.homebrew._show", AsyncMock()) as show:
            await form.finish(it, "x")
        assert show.await_args is not None
        text, view = show.await_args.args[1:]
        self.assertIn(MINE_NOTE, text)
        self.assertIn("now your own", text)
        self.assertIsInstance(view, EntryMenu)


def book(cid: str, name: str, edition: str | None, text: str = "The book's words.") -> Homebrew:
    return replace(brew(cid, name, text, kind="spell"), from_book=True, book_edition=edition)


class SharedBooks(LookupTest):
    """A DM's shared book ranks in its edition's place (#1563). Target 2024, fallback 2014."""

    async def shown(self, name: str, edition: str | None, who: int = DM) -> str:
        self.brew.entries[C1] = [book(C1, name, edition)]
        ui._last_lookup.clear()
        return self.sent(await self.command(name, who), 0)[0]

    async def test_a_2024_book_answers_with_the_target_rules(self) -> None:
        text = await self.shown("Fireball", "2024")
        self.assertIn("The book's words.", text)
        self.assertIn("[Shared book]", text)
        self.assertNotIn("[Legacy 2014]", text)
        self.assertIn("Frostmaiden's shared book (2024): Fireball", text)

    async def test_a_2014_or_other_book_sits_behind_a_target_hit(self) -> None:
        for edition in ("2014", "other", None):
            text = await self.shown("Fireball", edition)
            self.assertNotIn("The book's words.", text, edition)
            self.assertIn("📖 **Fireball** (spell)", text)  # the free rules answer

    async def test_a_2014_book_answers_and_is_tagged_when_only_the_fallback_has_it(self) -> None:
        text = await self.shown("Orc", "2014")
        self.assertIn("The book's words.", text)
        self.assertIn("[Shared book] [Legacy 2014]", text)
        self.assertIn("Frostmaiden's shared book (2014): Orc", text)
        self.assertNotIn("SRD", text)

    async def test_other_and_unasked_books_beat_the_fallback_but_not_the_target(self) -> None:
        for edition in ("other", None, "2024"):
            text = await self.shown("Orc", edition)
            self.assertIn("The book's words.", text, edition)
            self.assertNotIn("[Legacy 2014]", text)
        self.assertIn("shared book:", await self.shown("Orc", "other"))  # no edition to name

    async def test_with_no_free_rules_entry_any_book_answers(self) -> None:
        for edition in ("2024", "2014", "other", None):
            self.assertIn("The book's words.", await self.shown("Hollow King", edition))

    async def test_a_book_entry_answers_a_player_like_the_dm(self) -> None:
        self.brew.entries[C1] = [book(C1, "Hollow King", "2024")]
        ui._last_lookup.clear()
        text = self.sent(await self.command("Hollow King", PLAYER), 0)[0]
        self.assertIn("The book's words.", text)

    async def test_the_campaigns_rulesets_decide_where_a_2014_book_sits(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        text = await self.shown("Fireball", "2014")  # now the target: it answers, no tag
        self.assertIn("The book's words.", text)
        self.assertNotIn("[Legacy 2014]", text)

    async def test_the_homebrew_that_is_not_from_a_book_still_comes_first(self) -> None:
        self.brew.entries[C1] = [brew(C1, "Fireball", "Our fireball.", kind="spell")]
        ui._last_lookup.clear()
        self.assertIn("Our fireball.", self.sent(await self.command("Fireball"), 0)[0])


class HomebrewLookup(LookupTest):
    async def test_the_campaigns_homebrew_is_tagged_with_its_source_and_shadows_the_book(
        self,
    ) -> None:
        self.brew.entries[C1] = [brew(C1, "Goblin", "A very different goblin.")]
        it = await self.command("goblin")
        self.assertEqual(len(it.followup.send.await_args_list), 1)  # not the book's card too
        text = self.sent(it, 0)[0]
        self.assertIn("[Homebrew]", text)
        self.assertIn("Frostmaiden's homebrew: Goblin", text)
        self.assertIn("A very different goblin.", text)
        self.assertNotIn("SRD", text)

    async def test_a_name_only_the_campaign_has_is_answered_without_couldnt_find(self) -> None:
        self.brew.entries[C1] = [brew(C1, "Hollow King", "A crowned skeleton.")]
        it = await self.command("HOLLOW  king")
        self.assertEqual(len(it.followup.send.await_args_list), 1)
        text = self.sent(it, 0)[0]
        self.assertIn("Hollow King", text)
        self.assertNotIn("couldn't find", text)

    async def test_another_campaigns_homebrew_never_shows(self) -> None:
        self.campaigns[C2] = campaign(C2, "Strahd", dms=frozenset({DM}))
        self.brew.entries[C2] = [brew(C2, "Hollow King", "Strahd's secret.")]
        it = await self.command("Hollow King")
        self.assertEqual(self.brew.asked, [C1])  # only this campaign's entries were read
        self.assertNotIn("Strahd's secret", self.sent(it, 0)[0])
        self.assertIn("couldn't find", self.sent(it, 0)[0])

    async def test_a_player_reads_homebrew(self) -> None:
        self.brew.entries[C1] = [brew(C1, "Hollow King", "A crowned skeleton.")]
        it = await self.command("Hollow King", PLAYER)
        text = self.sent(it, 0)[0]
        self.assertIn("A crowned skeleton.", text)
        self.assertTrue(text.endswith(rule_card.PLAYER_LINE))

    async def test_a_homebrew_entry_shadows_the_book_for_a_player_too(self) -> None:
        self.brew.entries[C1] = [brew(C1, "Goblin", "Our goblin is secretly a dragon.")]
        it = await self.command("Goblin", PLAYER)
        text = self.sent(it, 0)[0]
        self.assertIn("dragon", text)
        self.assertNotIn("📖 **Goblin Warrior**", text)  # not the book's card too

    async def test_a_homebrew_store_that_cannot_be_read_still_shows_the_free_rules(self) -> None:
        self.brew.find = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        it = await self.command("goblin")
        self.assertIn("📖", self.sent(it, 0)[0])


class GlossaryLookup(LookupTest):
    """The Rules Glossary on /dmbot rule (#1891): found when no spell, condition or creature
    has the name, shown in the same card, with a house rule above it."""

    async def test_a_glossary_name_gives_its_card_with_source_and_page(self) -> None:
        it = await self.command("cover")
        text, _, kwargs = self.sent(it)
        self.assertIn("📖 **Cover** (rule)", text)
        self.assertIn("Source: SRD 5.2.1, Rules Glossary, p. 179", text)
        self.assertIn("Half Cover", text)
        self.assertTrue(kwargs["ephemeral"])

    async def test_a_tagged_entry_shows_its_tag_and_a_see_also_line_in_plain_words(self) -> None:
        text = self.sent(await self.command("dash"))[0]
        self.assertIn("📖 **Dash** (rule) [Action]", text)
        self.assertIn("See also: Speed", text)
        self.assertNotIn("http", text)

    async def test_a_spell_still_gives_the_spell(self) -> None:
        text = self.sent(await self.command("fireball"))[0]
        self.assertIn("📖 **Fireball** (spell)", text)

    async def test_a_name_that_is_a_spell_and_a_glossary_entry_gives_the_spell(self) -> None:
        text = self.sent(await self.command("darkness"))[0]
        self.assertIn("(spell)", text)
        self.assertNotIn("Rules Glossary", text)

    async def test_a_house_rule_on_the_name_is_shown_first(self) -> None:
        self.store.rules[C1] = [house(4, "Half cover gives +3 at our table", C1)]
        text = self.sent(await self.command("cover"))[0]
        self.assertTrue(text.startswith("🏠 **House rule 4:** Half cover gives +3"))
        self.assertLess(text.index("🏠"), text.index("📖 **Cover**"))

    async def test_a_long_entry_is_cut_into_parts_that_fit(self) -> None:
        it = await self.command("stat block")
        parts = [str(c.args[0]) for c in it.followup.send.await_args_list]
        self.assertTrue(parts)
        self.assertTrue(all(len(p) <= 2000 for p in parts))
        self.assertTrue(parts[0].startswith("📖 **Stat Block** (rule)"))

    async def test_a_name_in_nothing_is_still_no_match(self) -> None:
        text = self.sent(await self.command("blorpfrazzle"))[0]
        self.assertIn("blorpfrazzle", text)

    async def test_the_list_offers_glossary_names_from_three_letters_only(self) -> None:
        it = self.it(kind=discord.InteractionType.autocomplete)
        short = [c.name for c in await ui._typeahead(it, "co")]
        self.assertFalse([n for n in short if n.endswith("(rule)")])  # too short a word
        three = [c.name for c in await ui._typeahead(it, "cov")]
        self.assertIn("Cover (rule)", three)

    async def test_the_list_keeps_glossary_names_out_when_a_spell_begins_the_same_way(self) -> None:
        it = self.it(kind=discord.InteractionType.autocomplete)
        names = [c.name for c in await ui._typeahead(it, "dar")]
        self.assertIn("Darkness (spell)", names)
        self.assertFalse([n for n in names if n.endswith("(rule)")])
