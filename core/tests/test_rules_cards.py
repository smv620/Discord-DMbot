"""Rules cards from the table (#931): the limits (once a name, one a minute, Ignore), the
card that goes on the DM screen and nowhere else, its four buttons (who may press them),
the Settings button that turns it on (off by default, DMs only), and Override."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import Campaign
from dmbot.config import Settings
from dmbot.dm_screen import rules_cards
from dmbot.dm_screen.rules_cards import GAP_S, RulesCardButton, RulesCards
from dmbot.dm_screen.settings import RulesCardsButton, settings_text, settings_view
from dmbot.rules import index
from dmbot.rules.house import HouseRule
from dmbot.rules.spotter import Spotter
from dmbot.ui import house_rules as house_ui
from tests.test_memory_names import FakeResponse

GUILD, SCREEN, VOICE = 1, 30, 20
DM, PLAYER, SPEAKER = 7, 8, 9
C1 = "a1" * 16
NOW = 1_700_000_000


def campaign(
    on: bool = False, dms: frozenset[int] = frozenset({DM}), vis: str = "peek", **kw: Any
) -> Campaign:
    return Campaign(
        C1, GUILD, "Frostmaiden", NOW - 86400, NOW - 3600, "2024", "2014", True, dms, None,
        50, vis, rules_cards=on, **kw,
    )  # fmt: skip


def mentions(line: str) -> list[Any]:
    return Spotter.from_pool(index.srd().names_pool("2024", "2014")).find(line)


class Limits(unittest.TestCase):
    def test_a_name_gets_one_card_a_session(self) -> None:
        cards = RulesCards()
        first = cards.pick(mentions("I cast Fireball"), 1000.0)
        assert first is not None
        cards.remember(first, 1000.0)
        self.assertIsNone(cards.pick(mentions("another Fireball"), 1000.0 + GAP_S + 1))
        other = cards.pick(mentions("and Hold Person"), 1000.0 + GAP_S + 1)
        assert other is not None
        self.assertEqual(other.entry.name, "Hold Person")

    def test_one_card_a_minute_and_the_rest_are_dropped(self) -> None:
        cards = RulesCards()
        first = cards.pick(mentions("Fireball"), 1000.0)
        assert first is not None
        cards.remember(first, 1000.0)
        self.assertIsNone(cards.pick(mentions("Hold Person"), 1000.0 + GAP_S - 0.1))
        # the dropped name was not kept for later and was not counted as shown
        again = cards.pick(mentions("Hold Person"), 1000.0 + GAP_S)
        assert again is not None
        self.assertEqual(again.entry.name, "Hold Person")

    def test_the_first_card_needs_no_wait(self) -> None:
        self.assertIsNotNone(RulesCards().pick(mentions("Fireball"), 0.0))

    def test_ignore_means_no_more_cards_for_that_name(self) -> None:
        cards = RulesCards()
        first = cards.pick(mentions("Fireball"), 1000.0)
        assert first is not None
        card_id = cards.remember(first, 1000.0)
        gone = cards.ignore(card_id)
        assert gone is not None
        self.assertEqual(gone.name, "Fireball")
        self.assertIsNone(cards.pick(mentions("Fireball"), 9999.0))
        self.assertIsNone(cards.ignore("deadbeef"))  # an unknown card changes nothing

    def test_the_first_unseen_name_of_a_line_is_the_one(self) -> None:
        cards = RulesCards()
        first = cards.pick(mentions("Fireball then Hold Person"), 1000.0)
        assert first is not None
        cards.remember(first, 1000.0)
        second = cards.pick(mentions("Fireball then Hold Person"), 2000.0)
        assert second is not None
        self.assertEqual(second.entry.name, "Hold Person")


class FakeStore:
    """Like `HouseRuleStore`: only this campaign's rules, only DMs add."""

    def __init__(self) -> None:
        self.rules: dict[str, list[HouseRule]] = {}
        self.asked: list[str] = []

    async def list(self, guild_id: int, cid: str) -> list[HouseRule]:
        self.asked.append(cid)
        return list(self.rules.get(cid, []))

    async def add(
        self, guild_id: int, cid: str, user_id: int, text: str, instead: str | None = None
    ) -> HouseRule:
        if user_id != DM:
            from dmbot.rules import house

            raise house.HouseRuleError(house.NOT_DM)
        rule = HouseRule(len(self.rules.get(cid, [])) + 1, cid, text, instead, None, None,
                         user_id, NOW, NOW)  # fmt: skip
        self.rules.setdefault(cid, []).append(rule)
        return rule


class TableTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.consented = {SPEAKER}
        consent = SimpleNamespace(has_consent=lambda g, u: u in self.consented)
        self.campaigns = {C1: campaign(on=True)}
        self.saved = AsyncMock(
            side_effect=lambda g, cid, on: self.campaigns.update({cid: campaign(on=on)})
        )

        async def set_rules_cards(guild_id: int, cid: str, on: bool) -> Campaign:
            await self.saved(guild_id, cid, on)
            return self.campaigns[cid]

        stores = SimpleNamespace(
            get=AsyncMock(side_effect=lambda g, cid: self.campaigns.get(cid)),
            set_rules_cards=set_rules_cards,
        )
        self.store = FakeStore()
        self.bot = DMBot(Settings(discord_token="t", ears_secret="s"), consent, stores, MagicMock())  # type: ignore[arg-type]
        self.bot.house_rules = self.store  # type: ignore[assignment]
        self.posts: list[tuple[int, str, Any]] = []

        async def post_message(channel_id: int, text: str, view: Any = None) -> object:
            self.posts.append((channel_id, text, view))
            return object()

        self.bot.post_message = post_message  # type: ignore[assignment,method-assign]
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.table.listening = True
        self.table.rules_on = True
        self.table.campaign_id = C1
        self.table.dm_user_ids = frozenset({DM})
        self.bot.tables[GUILD] = self.table

    def rewind(self) -> None:
        """A minute goes by."""
        assert self.table.rules.last_at is not None
        self.table.rules.last_at -= GAP_S + 1

    async def say(self, line: str, user: int = SPEAKER) -> None:
        self.bot._note_rules(self.table, user, line)
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "rules-card":
                await task


class OnTheTable(TableTest):
    async def test_a_name_said_puts_a_card_on_the_dm_screen_only(self) -> None:
        await self.say("I cast Fireball at the door")
        (post,) = self.posts
        self.assertEqual(post[0], SCREEN)  # the DM screen: not the voice channel, no transcript
        self.assertIn("📖 **Fireball** (spell)", post[1])
        self.assertIn("_Heard: “I cast Fireball at the…”_", post[1])  # the words heard
        self.assertIn("Source: SRD 5.2.1, Spell Descriptions", post[1])
        self.assertIn("You decide.", post[1])
        self.assertLessEqual(len(post[1].splitlines()), 6)  # a card to glance at
        self.assertLessEqual(len(post[1]), 2000)
        labels = [str(b.item.label) for b in post[2].children]
        self.assertEqual(labels, ["Got it", "Ignore", "Override", "Read it all"])

    async def test_off_by_default_and_nothing_when_off(self) -> None:
        self.assertFalse(
            Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(1)).rules_on
        )
        self.assertFalse(campaign().rules_cards)
        self.table.rules_on = False
        await self.say("I cast Fireball")
        self.assertEqual(self.posts, [])

    async def test_nothing_after_the_session_is_over_or_the_audio_is_down(self) -> None:
        self.table.listening = False
        await self.say("I cast Fireball")
        self.table.listening = True
        del self.bot.tables[GUILD]  # /dmbot stop: the table is no longer the running one
        await self.say("I cast Fireball")
        self.assertEqual(self.posts, [])

    async def test_a_speaker_who_stopped_being_recorded_meanwhile_shows_nothing(self) -> None:
        self.bot._note_rules(self.table, SPEAKER, "I cast Fireball")
        self.consented.discard(SPEAKER)  # said no before the card was drawn
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "rules-card":
                await task
        self.assertEqual(self.posts, [])

    async def test_one_card_a_name_and_one_a_minute(self) -> None:
        await self.say("Fireball")
        await self.say("Fireball again")
        await self.say("and Hold Person")  # within the minute: dropped
        self.assertEqual(len(self.posts), 1)
        self.rewind()
        await self.say("Hold Person now")
        self.assertEqual(len(self.posts), 2)
        self.assertIn("**Hold Person**", self.posts[1][1])

    async def test_a_house_rule_of_this_campaign_comes_first(self) -> None:
        rule = HouseRule(12, C1, "Fireball also burns scrolls", None, None, None, DM, NOW, NOW)
        self.store.rules[C1] = [rule]
        self.store.rules["other"] = [
            HouseRule(1, "other", "Fireball is banned", None, None, None, DM, NOW, NOW)
        ]
        await self.say("I cast Fireball")
        text = self.posts[0][1]
        self.assertTrue(text.startswith("🏠 **House rule 12:** Fireball also burns scrolls"))
        self.assertNotIn("banned", text)
        self.assertEqual(self.store.asked, [C1])

    async def test_an_older_creature_carries_its_tag(self) -> None:
        await self.say("the Orc charges")
        text = self.posts[0][1]
        self.assertIn("[Legacy 2014]", text)
        self.assertIn("Source: SRD 5.1, Monsters, p. 339 [Legacy 2014]", text)

    async def test_no_card_for_everyday_words(self) -> None:
        await self.say("a light in the dark, the cat, a bat, he fell prone")
        self.assertEqual(self.posts, [])
        await self.say("the goblin is grappled")
        self.assertEqual(len(self.posts), 1)  # the goblin; Grappled waits a minute

    async def test_it_is_only_ever_posted_to_the_dm_screen(self) -> None:
        await self.say("I cast Fireball")
        self.assertEqual({channel for channel, _, _ in self.posts}, {SCREEN})  # not the voice
        # channel's players, not the transcript channel: the only channel it knows is the screen
        self.table.transcript_channel_id = 55
        self.rewind()
        await self.say("and Hold Person")
        self.assertEqual({channel for channel, _, _ in self.posts}, {SCREEN})


class WhenThingsGoWrong(TableTest):
    async def test_a_bug_in_the_cards_never_loses_the_transcript_line(self) -> None:
        from unittest.mock import patch

        from dmbot.audio.segmenter import Utterance

        said = Utterance(GUILD, SPEAKER, 0, 0, bytes(32000), self.table.segmenter.session)
        with patch.object(Spotter, "find", side_effect=RuntimeError("boom")):
            self.bot._deliver_transcript(said, "Then I cast Fireball")
        self.assertEqual(self.table.heard, [(SPEAKER, "Then I cast Fireball")])  # still kept
        self.assertEqual(self.posts, [])

    async def test_a_card_that_could_not_be_posted_gives_back_its_name_and_its_minute(self) -> None:
        async def nowhere(channel_id: int, text: str, view: Any = None) -> None:
            return None

        self.bot.post_message = nowhere  # type: ignore[method-assign]
        before = self.table.rules.last_at
        await self.say("I cast Fireball")
        self.assertEqual(self.table.rules.seen, set())
        self.assertEqual(self.table.rules.last_at, before)
        self.assertEqual(self.table.rules.shown, {})

    async def test_a_speaker_who_said_no_meanwhile_gives_them_back_too(self) -> None:
        self.bot._note_rules(self.table, SPEAKER, "I cast Fireball")
        self.consented.discard(SPEAKER)
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "rules-card":
                await task
        self.assertEqual(self.table.rules.seen, set())

    async def test_house_rules_that_fail_or_hang_still_give_the_card(self) -> None:
        from unittest.mock import patch

        self.bot.house_rules = SimpleNamespace(list=AsyncMock(side_effect=RuntimeError("down")))  # type: ignore[assignment]
        await self.say("I cast Fireball")
        self.assertEqual(len(self.posts), 1)

        async def hang(*_: Any) -> list[HouseRule]:
            await asyncio.sleep(60)
            return []

        self.rewind()
        self.bot.house_rules = SimpleNamespace(list=hang)  # type: ignore[assignment]
        with patch("dmbot.bot.RULES_CARD_DB_S", 0.05):
            await self.say("and Hold Person")
        self.assertEqual(len(self.posts), 2)

    async def test_the_card_shown_is_the_entry_spotted(self) -> None:
        await self.say("the goblins attack")
        self.assertIn("📖 **Goblin Warrior**", self.posts[0][1])
        self.assertIn("Heard: “the goblins attack”", self.posts[0][1])
        self.assertNotIn("you typed", self.posts[0][1])  # nobody typed anything

    async def test_the_names_are_looked_up_off_the_loop_when_it_is_turned_on(self) -> None:
        from unittest.mock import patch

        self.campaigns[C1] = campaign(on=False)
        self.table.rules_on = False
        with patch.object(self.bot, "_warm_rules") as warm:
            await self.bot.set_rules_cards(GUILD, C1, True)
            warm.assert_called_once_with(self.table.rules_rulesets)
            warm.reset_mock()
            await self.bot.set_rules_cards(GUILD, C1, False)
            warm.assert_not_called()


class FromTheTranscript(TableTest):
    async def test_a_delivered_line_gets_its_card_through_the_real_hook(self) -> None:
        from dmbot.audio.segmenter import Utterance

        said = Utterance(GUILD, SPEAKER, 0, 0, bytes(32000), self.table.segmenter.session)
        self.bot._deliver_transcript(said, "Then I cast Fireball")
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "rules-card":
                await task
        (post,) = self.posts
        self.assertIn("📖 **Fireball**", post[1])
        self.assertEqual(post[0], SCREEN)

    async def test_the_line_that_has_no_name_or_comes_with_the_setting_off_shows_nothing(
        self,
    ) -> None:
        from dmbot.audio.segmenter import Utterance

        said = Utterance(GUILD, SPEAKER, 0, 0, bytes(32000), self.table.segmenter.session)
        self.bot._deliver_transcript(said, "We walk to the inn")
        self.table.rules_on = False
        self.bot._deliver_transcript(said, "Then I cast Fireball")
        await asyncio.sleep(0)
        self.assertEqual(self.posts, [])


class Buttons(TableTest):
    async def card(self) -> tuple[str, Any]:
        await self.say("I cast Fireball")
        (_, text, view) = self.posts[0]
        return text, view

    def interaction(self, user_id: int = DM, guild: int = GUILD, content: str = "card") -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=guild),
            guild_id=guild,
            user=user,
            type=discord.InteractionType.component,
            message=SimpleNamespace(content=content),
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    @staticmethod
    def button(view: Any, label: str) -> Any:
        return next(b for b in view.children if str(b.item.label) == label)

    async def test_got_it_takes_the_buttons_away(self) -> None:
        _, view = await self.card()
        it = self.interaction()
        await self.button(view, "Got it").callback(it)
        self.assertEqual(it.response.edited, [("", None)])

    async def test_ignore_takes_the_buttons_away_and_stops_that_name(self) -> None:
        text, view = await self.card()
        it = self.interaction(content=text)
        await self.button(view, "Ignore").callback(it)
        content, new_view = it.response.edited[0]
        self.assertIsNone(new_view)
        self.assertTrue(content.startswith(text))
        self.assertIn("No more cards for Fireball this session.", content)
        self.assertIn(rules_cards.STOP_ALL, content)  # and how to stop them all
        self.rewind()
        await self.say("Fireball once more")
        self.assertEqual(len(self.posts), 1)

    async def test_override_opens_the_house_rule_form_with_the_name_filled_in(self) -> None:
        _, view = await self.card()
        it = self.interaction()
        it.response.send_modal = AsyncMock()
        await self.button(view, "Override").callback(it)
        form = it.response.send_modal.await_args.args[0]
        self.assertIsInstance(form, house_ui.OverrideForm)
        self.assertEqual(form.instead.default, "Fireball")
        self.assertEqual(form.campaign.id, C1)

    async def test_read_it_all_sends_the_full_card_privately(self) -> None:
        _, view = await self.card()
        it = self.interaction()
        await self.button(view, "Read it all").callback(it)
        text = str(it.followup.send.await_args.args[0])
        self.assertIn("📖 **Fireball** (spell)", text)
        self.assertIn("8d6 Fire damage", text)  # the whole rule
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])

    async def test_a_player_is_refused_every_button(self) -> None:
        _, view = await self.card()
        for label in ("Got it", "Ignore", "Override", "Read it all"):
            it = self.interaction(PLAYER)
            it.response.send_modal = AsyncMock()
            await self.button(view, label).callback(it)
            with self.subTest(label):
                self.assertEqual(it.response.sent[0][0], rules_cards.ONLY_DMS)
                it.response.send_modal.assert_not_awaited()
                self.assertEqual(it.response.edited, [])
        self.assertEqual(len(self.table.rules.ignored), 0)

    async def test_someone_who_stopped_being_a_dm_meanwhile_is_refused(self) -> None:
        _, view = await self.card()
        self.campaigns[C1] = campaign(on=True, dms=frozenset({PLAYER}))
        it = self.interaction(DM)
        await self.button(view, "Got it").callback(it)
        self.assertEqual(it.response.sent[0][0], rules_cards.ONLY_DMS)

    async def test_after_the_session_or_a_restart_a_press_says_it_is_closed(self) -> None:
        _, view = await self.card()
        del self.bot.tables[GUILD]
        it = self.interaction()
        await self.button(view, "Got it").callback(it)
        self.assertEqual(it.response.sent[0][0], rules_cards.CLOSED)
        # another server's button press, whatever its id says
        self.bot.tables[GUILD] = self.table
        it = self.interaction(guild=99)
        await self.button(view, "Got it").callback(it)
        self.assertEqual(it.response.sent[0][0], rules_cards.CLOSED)
        # a card that never existed
        it = self.interaction()
        await RulesCardButton(GUILD, "deadbeef", "got").callback(it)
        self.assertEqual(it.response.sent[0][0], rules_cards.CLOSED)

    async def test_ids_survive_a_restart_and_fit_a_phone(self) -> None:
        _, view = await self.card()
        template = RulesCardButton.__discord_ui_compiled_template__
        for item in view.children:
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))
            self.assertLessEqual(len(str(item.item.label)), 25)
            self.assertLessEqual(len(str(item.item.custom_id)), 100)


class Override(TableTest):
    def form(self) -> house_ui.OverrideForm:
        form = house_ui.OverrideForm(self.campaigns[C1], "Fireball")
        form.rule._value = "Fireball only burns what you aim at"
        form.instead._value = str(form.instead.default)  # the DM leaves the filled-in box as it is
        return form

    def submit_interaction(self, user_id: int = DM) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            type=discord.InteractionType.modal_submit,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_it_adds_the_rule_and_answers_privately_without_changing_the_card(self) -> None:
        it = self.submit_interaction()
        await self.form().on_submit(it)
        (rule,) = self.store.rules[C1]
        self.assertEqual(
            (rule.rule, rule.supersedes), ("Fireball only burns what you aim at", "Fireball")
        )
        text = str(it.followup.send.await_args.args[0])
        self.assertIn("Added house rule 1", text)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])
        it.edit_original_response.assert_not_awaited()  # the card on the DM screen is untouched

    async def test_a_player_cannot_add_a_rule_this_way(self) -> None:
        it = self.submit_interaction(PLAYER)
        await self.form().on_submit(it)
        self.assertEqual(self.store.rules, {})
        self.assertIn("Only", str(it.followup.send.await_args.args[0]))


class Setting(TableTest):
    def press(self, user_id: int) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_it_is_off_until_a_dm_turns_it_on(self) -> None:
        text = settings_text(campaign(on=False), None, DM)
        self.assertIn("**Rules cards: Off.**", text)
        self.assertIn("You decide what applies", text)
        self.assertIn("free rules (SRD)", text)
        on = settings_text(campaign(on=True), None, DM)
        self.assertIn("**Rules cards: On.**", on)
        self.assertNotIn("Players can see", on)
        self.assertIn(
            "Players can see the cards", settings_text(campaign(on=True, vis="open"), None, DM)
        )
        labels = {c.item.label for c in settings_view(campaign(), None, DM).children}  # type: ignore[attr-defined]
        self.assertIn("Turn rules cards on", labels)

    async def test_the_button_is_only_for_the_campaigns_dms(self) -> None:
        for viewer, expected in ((DM, True), (PLAYER, False)):
            items = [
                getattr(c, "item", c) for c in settings_view(campaign(), None, viewer).children
            ]
            labels = [str(getattr(i, "label", "")) for i in items]
            self.assertEqual("Turn rules cards on" in labels, expected, viewer)

    async def test_a_dm_press_turns_it_on_here_and_on_the_running_table(self) -> None:
        self.campaigns[C1] = campaign(on=False)
        self.table.rules_on = False
        it = self.press(DM)
        await RulesCardsButton(C1, False).callback(it)  # shows "Off"; a press turns it on
        self.saved.assert_awaited_once_with(GUILD, C1, True)
        self.assertTrue(self.table.rules_on)  # the live session follows at once
        it.edit_original_response.assert_awaited()  # the card shows the new choice

    async def test_a_press_when_on_turns_it_off(self) -> None:
        it = self.press(DM)
        await RulesCardsButton(C1, True).callback(it)
        self.saved.assert_awaited_once_with(GUILD, C1, False)
        self.assertFalse(self.table.rules_on)

    async def test_a_player_or_a_manager_who_is_not_a_dm_cannot_change_it(self) -> None:
        for who in (PLAYER, 12345):
            it = self.press(who)
            await RulesCardsButton(C1, False).callback(it)
            self.saved.assert_not_awaited()
            self.assertIn("Only", it.response.sent[0][0])
        self.assertTrue(self.table.rules_on)

    async def test_ids_survive_a_restart(self) -> None:
        template = RulesCardsButton.__discord_ui_compiled_template__
        for on in (False, True):
            button = RulesCardsButton(C1, on)
            match = template.fullmatch(str(button.item.custom_id))
            assert match is not None
            self.assertEqual(match["to"], "off" if on else "on")
            self.assertLessEqual(len(str(button.item.label)), 25)

    async def test_a_session_starting_later_reads_the_saved_setting(self) -> None:
        self.assertTrue(campaign(on=True).rules_cards)
        table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.assertFalse(table.rules_on)  # a table only turns it on from the campaign


class Words(unittest.TestCase):
    def test_the_words_are_plain(self) -> None:
        for text in (rules_cards.CLOSED, rules_cards.ONLY_DMS):
            self.assertNotRegex(text, r"(?i)\b(srd|ruleset|index|query|database)\b")
        self.assertIn("press 📖 Look up a rule", rules_cards.CLOSED)  # what to do next
        self.assertIn("Rules cards", rules_cards.STOP_ALL)  # how to stop them
        self.assertEqual(rules_cards.ONLY_DMS.count("DMs"), 1)


if __name__ == "__main__":
    unittest.main()
