"""Looking up a rule (#908): `/dmbot rule`, the 📖 button on the Settings card and its form.
Only a campaign's DMs, the answer private, the campaign's own house rules and no one else's,
and no match offers names without picking one."""

from __future__ import annotations

import asyncio
import time
import unittest
import unittest.mock
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign
from dmbot.dm_screen.settings import RuleLookupButton, settings_view
from dmbot.rules import index
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


class LookupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.campaigns = {C1: campaign()}
        self.store = FakeStore()
        self.playing: str | None = C1
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(
                get=AsyncMock(side_effect=lambda g, cid: self.campaigns.get(cid)),
                list_campaigns=AsyncMock(side_effect=lambda g: list(self.campaigns.values())),
            ),
            house_rules=self.store,
            active_campaign_id=lambda guild_id: self.playing,
        )

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

    async def test_a_player_is_refused_and_sees_nothing(self) -> None:
        it = await self.command("fireball", PLAYER)
        text, _, kwargs = self.sent(it)
        self.assertEqual(text, ui.ONLY_DMS_ANY)
        self.assertTrue(kwargs["ephemeral"])
        self.assertNotIn("Fireball", text)
        self.assertEqual(self.store.asked, [])  # not even the house rules were read

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

    async def test_a_player_pressing_an_old_name_button_is_refused(self) -> None:
        it = await self.command("Firball")
        view = self.sent(it)[1]
        pressed = self.it(PLAYER)
        await self.press(view, "Fireball").callback(pressed)
        self.assertEqual(self.sent(pressed)[0], ui.ONLY_DMS)

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
        self.assertEqual(form.name.label, "Spell, condition or creature")
        self.assertLessEqual(len(str(form.name.label)), 45)
        self.assertLessEqual(len(ui.FORM_TITLE), 45)

    async def test_it_gives_the_card(self) -> None:
        it = await self.submit(ui.LookupForm(self.campaigns[C1]), "  grappled ")
        self.assertIn("📖 **Grappled** (condition)", self.sent(it)[0])

    async def test_an_empty_box_says_what_to_do(self) -> None:
        it = await self.submit(ui.LookupForm(self.campaigns[C1]), "   ")
        self.assertEqual(self.sent(it)[0], ui.EMPTY)

    async def test_someone_who_stopped_being_a_dm_meanwhile_is_refused(self) -> None:
        form = ui.LookupForm(self.campaigns[C1])
        self.campaigns[C1] = campaign(dms=frozenset({OTHER_DM}))
        it = await self.submit(form, "fireball")
        self.assertEqual(self.sent(it)[0], ui.ONLY_DMS)

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
        self.assertLess(time.perf_counter() - started, 0.5)  # 5 keystrokes, with room to spare

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

    async def test_a_player_gets_names_but_not_the_campaigns_choice_of_rules(self) -> None:
        self.campaigns[C1] = campaign(target="2014", fallback="2024")
        it = self.it(PLAYER, kind=discord.InteractionType.autocomplete)
        values = [c.value for c in await ui._typeahead(it, "goblin")]
        self.assertEqual(values[0], "Goblin Warrior")  # the defaults, not this campaign's


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
        for text in (ui.ONLY_DMS, ui.ONLY_DMS_ANY, ui.EMPTY, ui.TOO_LONG, ui.WHICH_CAMPAIGN):
            self.assertNotRegex(text, r"(?i)\b(srd|ruleset|index|query|database)\b")
        self.assertIn("free rules (SRD)", rule_card.NOT_A_RULING)
        self.assertEqual(rule_card.FREE_RULES, "the free rules (SRD)")


if __name__ == "__main__":
    unittest.main()
