"""A house rule said at the table (#953): the phrases (and what must not start one), the
proposal on the DM screen, Save / Edit / Cancel and Keep both / Replace, DMs only, the
limits, and that nothing is saved without a press."""

from __future__ import annotations

import ast
import asyncio
import inspect
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot import bot as bot_module
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.bot import DMBot, Table
from dmbot.campaigns import Campaign
from dmbot.config import Settings
from dmbot.dm_screen import house_voice as screen
from dmbot.dm_screen.house_voice import HouseVoiceButton
from dmbot.rules import house
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.house_voice import GAP_S, Clash, HouseVoice, Proposal, Said, find
from dmbot.ui import house_rules as house_ui
from tests.test_memory_names import FakeResponse

GUILD, SCREEN, VOICE = 1, 30, 20
DM, PLAYER = 7, 8
C1 = "a1" * 16
NOW = 1_700_000_000


def campaign(dms: frozenset[int] = frozenset({DM})) -> Campaign:
    return Campaign(C1, GUILD, "Frostmaiden", NOW - 86400, NOW - 3600, "2024", "2014", True,
                    dms, None, 50, "peek")  # fmt: skip


class Phrases(unittest.TestCase):
    def test_the_phrases_that_start_a_house_rule(self) -> None:
        cases = {
            "house rule: potions are a bonus action": "Potions are a bonus action",
            "House rule, crits double the dice.": "Crits double the dice.",
            "house rule - nobody dies on the first fall": "Nobody dies on the first fall",
            "new house rule falling damage is halved": "Falling damage is halved",
            "New house rule: no evil gods": "No evil gods",
            "for this table, rest takes a day": "Rest takes a day",
            "For this table: no flanking": "No flanking",
            "our rule is you can't buy magic items": "You can't buy magic items",
            "Our house rule is: spells need a free hand": "Spells need a free hand",
        }
        for line, rule in cases.items():
            with self.subTest(line):
                said = find(line)
                assert said is not None
                self.assertEqual((said.rule, said.cut), (rule, False))

    def test_what_must_not_start_one(self) -> None:
        for line in (
            "the house rules say we can do that",
            "what is the house rule for potions",
            "house rules: ok",  # plural: not the phrase
            "do we have a house rule: potions",  # not at the start
            "okay so house rule: potions are a bonus action",  # not at the start
            "house rule",
            "house rule: yes",  # too short to be a rule
            "for this table I think",  # no delimiter
            "our rule isn't known",
            "",
            "   ",
        ):
            with self.subTest(line):
                self.assertIsNone(find(line))

    def test_a_long_rule_is_cut_at_a_word_and_says_so(self) -> None:
        said = find("house rule: " + "word " * 200)
        assert said is not None
        self.assertTrue(said.cut)
        self.assertLessEqual(len(said.rule), house.RULE_MAX)
        self.assertTrue(said.rule.endswith("word"))

    def test_the_same_words_are_the_same_key(self) -> None:
        a, b = (
            find("house rule: Potions are a bonus action."),
            find("new house rule potions are a bonus  action"),
        )
        assert a is not None and b is not None
        self.assertEqual(a.key, b.key)


class Limits(unittest.TestCase):
    def test_one_a_minute_and_the_same_words_once(self) -> None:
        voice = HouseVoice()
        first = voice.pick(find("house rule: potions are a bonus action"), 1000.0)
        assert first is not None
        voice.remember(first, 1000.0, "aaaa1111", Proposal(first))
        self.assertIsNone(voice.pick(find("house rule: no flanking at all"), 1000.0 + GAP_S - 1))
        self.assertIsNone(voice.pick(find("house rule: potions are a bonus action"), 5000.0))
        self.assertIsNotNone(voice.pick(find("house rule: no flanking at all"), 1000.0 + GAP_S))
        self.assertIsNone(voice.pick(None, 9999.0))

    def test_a_proposal_that_fails_gives_back_its_words_and_its_minute(self) -> None:
        voice = HouseVoice()
        said = voice.pick(find("house rule: potions are a bonus action"), 1000.0)
        assert said is not None
        voice.remember(said, 1000.0, "aaaa1111", Proposal(said))
        voice.forget("aaaa1111", None, 1000.0)
        self.assertEqual((voice.seen, voice.last_at, voice.proposals), (set(), None, {}))
        # a later proposal's minute is not undone
        voice.remember(said, 1000.0, "aaaa1111", Proposal(said))
        other = find("house rule: no flanking at all")
        assert other is not None
        voice.remember(other, 2000.0, "bbbb2222", Proposal(other))
        voice.forget("aaaa1111", None, 1000.0)
        self.assertEqual(voice.last_at, 2000.0)


class FakeStore:
    """Like `HouseRuleStore`: only DMs write, numbers are never reused, a change made from
    an old view is refused."""

    def __init__(self) -> None:
        self.rules: list[HouseRule] = []
        self.made = 0
        self.added: list[dict[str, Any]] = []

    def seed(self, text: str, instead: str | None = None) -> HouseRule:
        self.made += 1
        rule = HouseRule(self.made, C1, text, instead, None, None, DM, NOW, NOW)
        self.rules.append(rule)
        return rule

    async def list(self, guild_id: int, cid: str) -> list[HouseRule]:
        return sorted(self.rules, key=lambda r: -r.number)

    async def add(self, guild_id: int, cid: str, user_id: int, text: str,
                  instead: str | None = None, *, scenario: str | None = None,
                  session_id: str | None = None) -> HouseRule:  # fmt: skip
        if user_id != DM:
            raise HouseRuleError(house.NOT_DM)
        self.made += 1
        rule = HouseRule(self.made, cid, house.clean_rule(text),
                         house.clean_optional(instead, house.INSTEAD_BOX), scenario, session_id,
                         user_id, NOW, NOW)  # fmt: skip
        self.rules.append(rule)
        self.added.append({"rule": rule, "scenario": scenario, "session_id": session_id})
        return rule

    async def edit(
        self, guild_id: int, cid: str, user_id: int, number: int, text: str,
        instead: str | None = None, *, unchanged_since: int | None = None,
    ) -> HouseRule:  # fmt: skip
        if user_id != DM:
            raise HouseRuleError(house.NOT_DM)
        for i, rule in enumerate(self.rules):
            if rule.number == number:
                if unchanged_since is not None and rule.version != unchanged_since:
                    raise HouseRuleError(house.CHANGED)
                changed = HouseRule(
                    number, cid, text, instead, rule.scenario, rule.session_id,
                    rule.created_by, rule.created_at, NOW + 1, rule.version + 1,
                )  # fmt: skip
                self.rules[i] = changed
                return changed
        raise HouseRuleError(house.GONE)


class TableTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.consented = {DM, PLAYER}
        consent = SimpleNamespace(has_consent=lambda g, u: u in self.consented)
        self.campaigns = {C1: campaign()}
        stores = SimpleNamespace(get=AsyncMock(side_effect=lambda g, cid: self.campaigns.get(cid)))
        self.store = FakeStore()
        self.bot = DMBot(Settings(discord_token="t", ears_secret="s"), consent, stores, MagicMock())  # type: ignore[arg-type]
        self.bot.house_rules = self.store  # type: ignore[assignment]
        self.posts: list[tuple[int, str, Any]] = []

        async def post_message(channel_id: int, text: str, view: Any = None) -> object:
            self.posts.append((channel_id, text, view))
            return object()

        self.bot.post_message = post_message  # type: ignore[method-assign,assignment]
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.table.listening = True
        self.table.campaign_id = C1
        self.table.dm_user_ids = frozenset({DM})
        self.table.transcript_session_id = "session-1"
        self.bot.tables[GUILD] = self.table

    async def settle(self) -> None:
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "house-rule-voice":
                await task

    async def deliver(self, line: str, user: int = DM) -> None:
        said = Utterance(GUILD, user, 0, 0, bytes(32000), self.table.segmenter.session)
        self.bot._deliver_transcript(said, line)
        await self.settle()

    def interaction(self, user_id: int = DM, content: str = "proposal") -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        return SimpleNamespace(
            client=self.bot, guild=SimpleNamespace(id=GUILD), guild_id=GUILD, user=user,
            message=SimpleNamespace(content=content), response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
        )  # fmt: skip

    @staticmethod
    def button(view: Any, label: str) -> Any:
        return next(b for b in view.children if str(b.item.label) == label)

    def rewind(self) -> None:
        assert self.table.house_voice.last_at is not None
        self.table.house_voice.last_at -= GAP_S + 1


class Proposing(TableTest):
    async def test_a_dms_house_rule_becomes_a_proposal_on_the_dm_screen_only(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        (post,) = self.posts
        self.assertEqual(post[0], SCREEN)
        self.assertIn(
            "🏠 **Save as a house rule?** You said: “Potions are a bonus action”", post[1]
        )
        self.assertIn("Nothing is saved unless you press **Save**", post[1])
        self.assertEqual(
            [str(b.item.label) for b in post[2].children], ["Save rule", "Edit", "Cancel"]
        )
        self.assertEqual(self.store.added, [])  # not saved until a press
        self.assertLessEqual(len(post[1]), 2000)

    async def test_a_players_line_never_starts_one(self) -> None:
        await self.deliver("house rule: potions are a bonus action", user=PLAYER)
        await self.deliver("our rule is that I always go first", user=PLAYER)
        self.assertEqual(self.posts, [])

    async def test_questions_and_other_talk_start_nothing(self) -> None:
        for line in ("the house rules say we can do that", "what is the house rule for potions"):
            await self.deliver(line)
        self.assertEqual(self.posts, [])

    async def test_the_limits(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        await self.deliver("house rule: no flanking at all")  # within the minute: dropped
        self.rewind()
        await self.deliver("house rule: potions are a bonus action.")  # the same words again
        self.assertEqual(len(self.posts), 1)
        await self.deliver("house rule: no flanking at all")
        self.assertEqual(len(self.posts), 2)

    async def test_nothing_when_the_session_is_over_or_the_dm_said_no_meanwhile(self) -> None:
        self.table.listening = False
        await self.deliver("house rule: potions are a bonus action")
        self.table.listening = True
        self.consented.discard(DM)
        await self.deliver("house rule: potions are a bonus action")
        self.assertEqual(self.posts, [])
        self.assertEqual(self.table.house_voice.seen, set())  # given back

    async def test_a_failed_post_gives_back_the_words(self) -> None:
        async def nowhere(channel_id: int, text: str, view: Any = None) -> None:
            return None

        self.bot.post_message = nowhere  # type: ignore[method-assign]
        await self.deliver("house rule: potions are a bonus action")
        self.assertEqual(self.table.house_voice.seen, set())
        self.assertIsNone(self.table.house_voice.last_at)

    async def test_a_bug_in_it_never_loses_the_transcript_line(self) -> None:
        from unittest.mock import patch

        self.bot.transcripts = MagicMock()
        with patch.object(house_voice_module, "find", side_effect=RuntimeError("boom")):
            await self.deliver("house rule: potions are a bonus action")
        (line,) = self.table.unsaved.take(lambda _user: True)
        self.assertEqual(line.heard, "house rule: potions are a bonus action")

    async def test_the_dms_line_stays_in_the_transcript_and_the_proposal_never_does(self) -> None:
        self.bot.transcripts = MagicMock()
        await self.deliver("house rule: potions are a bonus action")
        (line,) = self.table.unsaved.take(lambda _user: True)
        self.assertEqual(line.heard, "house rule: potions are a bonus action")  # as said
        self.assertEqual({channel for channel, _, _ in self.posts}, {SCREEN})
        self.assertNotIn("house rule?", line.text)


class Pressing(TableTest):
    async def proposal(self, line: str = "house rule: potions are a bonus action") -> Any:
        await self.deliver(line)
        return self.posts[-1][2]

    async def test_save_writes_the_next_house_rule_with_where_it_came_from(self) -> None:
        view = await self.proposal()
        it = self.interaction(content=self.posts[0][1])
        await self.button(view, "Save rule").callback(it)
        (added,) = self.store.added
        self.assertEqual(added["rule"].rule, "Potions are a bonus action")
        self.assertRegex(added["scenario"], r"^Said at the table, \d{4}-\d{2}-\d{2}$")
        self.assertEqual(added["session_id"], "session-1")
        content, new_view = it.response.edited[0]
        self.assertIsNone(new_view)
        self.assertIn("✅ Saved as house rule 1.", content)
        self.assertTrue(content.startswith("🏠 **Save as a house rule?**"))
        self.assertEqual(self.table.house_voice.proposals, {})

    async def test_cancel_saves_nothing_and_takes_the_buttons_away(self) -> None:
        view = await self.proposal()
        it = self.interaction()
        await self.button(view, "Cancel").callback(it)
        self.assertEqual(self.store.added, [])
        self.assertIsNone(it.response.edited[0][1])
        self.assertIn("Cancelled", it.response.edited[0][0])

    async def test_edit_opens_the_add_form_with_the_words_filled_in_and_saves_it(self) -> None:
        view = await self.proposal()
        it = self.interaction()
        it.response.send_modal = AsyncMock()
        await self.button(view, "Edit").callback(it)
        form = it.response.send_modal.await_args.args[0]
        self.assertIsInstance(form, house_ui.ProposalForm)
        self.assertIsInstance(form, house_ui.AddForm)  # the same form as Add
        self.assertEqual(form.rule.default, "Potions are a bonus action")
        self.assertFalse(form.instead.default)
        form.rule._value = "Potions are a free action"
        form.instead._value = ""
        submit = self.interaction(content=self.posts[0][1])
        await form.on_submit(submit)
        (added,) = self.store.added
        self.assertEqual(added["rule"].rule, "Potions are a free action")
        self.assertEqual(added["session_id"], "session-1")
        self.assertIn("✅ Saved as house rule 1.", submit.response.edited[0][0])
        self.assertIsNone(submit.response.edited[0][1])

    async def test_an_edit_by_someone_who_is_not_a_dm_saves_nothing(self) -> None:
        view = await self.proposal()
        (proposal_id,) = self.table.house_voice.proposals
        form = house_ui.ProposalForm(
            campaign(), self.table.house_voice.proposals[proposal_id], proposal_id
        )
        form.rule._value = "Potions are a free action"
        form.instead._value = ""
        submit = self.interaction(PLAYER)
        await form.on_submit(submit)
        self.assertEqual(self.store.added, [])
        self.assertTrue(submit.response.sent)
        del view

    async def test_a_player_is_refused_every_button(self) -> None:
        view = await self.proposal()
        for label in ("Save rule", "Edit", "Cancel"):
            it = self.interaction(PLAYER)
            it.response.send_modal = AsyncMock()
            await self.button(view, label).callback(it)
            with self.subTest(label):
                self.assertEqual(it.response.sent[0][0], screen.ONLY_DMS)
                it.response.send_modal.assert_not_awaited()
                self.assertEqual(it.response.edited, [])
        self.assertEqual(self.store.added, [])
        self.assertEqual(len(self.table.house_voice.proposals), 1)  # still there

    async def test_someone_who_stopped_being_a_dm_meanwhile_is_refused(self) -> None:
        view = await self.proposal()
        self.campaigns[C1] = campaign(dms=frozenset({PLAYER}))
        it = self.interaction(DM)
        await self.button(view, "Save rule").callback(it)
        self.assertEqual(it.response.sent[0][0], screen.ONLY_DMS)
        self.assertEqual(self.store.added, [])

    async def test_after_the_session_or_a_restart_a_press_says_it_is_closed(self) -> None:
        view = await self.proposal()
        del self.bot.tables[GUILD]
        it = self.interaction()
        await self.button(view, "Save rule").callback(it)
        self.assertEqual(it.response.sent[0][0], screen.CLOSED)
        self.bot.tables[GUILD] = self.table
        it = self.interaction()
        it.guild_id = 99  # another server's press
        await self.button(view, "Save rule").callback(it)
        self.assertEqual(it.response.sent[0][0], screen.CLOSED)
        it = self.interaction()
        await HouseVoiceButton(GUILD, "deadbeef", "save").callback(it)
        self.assertEqual(it.response.sent[0][0], screen.CLOSED)
        self.assertEqual(self.store.added, [])

    async def test_a_full_list_is_told_and_nothing_goes_wrong(self) -> None:
        view = await self.proposal()
        self.store.add = AsyncMock(side_effect=HouseRuleError(house.FULL))  # type: ignore[method-assign]
        it = self.interaction()
        await self.button(view, "Save rule").callback(it)
        self.assertEqual(it.response.sent[0][0], house.FULL)
        self.assertEqual(it.response.edited, [])  # the buttons stay for another try

    async def test_ids_survive_a_restart_and_fit_a_phone(self) -> None:
        view = await self.proposal()
        template = HouseVoiceButton.__discord_ui_compiled_template__
        for item in view.children:
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))
            self.assertLessEqual(len(str(item.item.label)), 25)
            self.assertLessEqual(len(str(item.item.custom_id)), 100)


class Conflicts(TableTest):
    async def test_a_rule_about_the_same_spell_makes_both_shown_with_other_buttons(self) -> None:
        self.store.seed("Fireball burns scrolls", "Fireball only damages")
        self.store.seed("Nobody may rest in a dungeon")
        await self.deliver("house rule: Fireball is a bonus action")
        (post,) = self.posts

        self.assertIn("House rule 1 mentions the same thing: “Fireball burns scrolls”", post[1])
        self.assertNotIn("House rule 2", post[1])  # not about Fireball
        self.assertIn("**Replace rule 1** swaps its words", post[1])
        self.assertEqual(
            [str(b.item.label) for b in post[2].children],
            ["Save as new rule", "Replace rule 1", "Cancel"],
        )

    async def test_no_clash_when_the_words_name_nothing_in_common(self) -> None:
        self.store.seed("Fireball burns scrolls")
        await self.deliver("house rule: potions are a bonus action")
        self.assertEqual(
            [str(b.item.label) for b in self.posts[0][2].children], ["Save rule", "Edit", "Cancel"]
        )

    async def test_another_campaigns_rules_never_clash(self) -> None:
        other = HouseRule(1, "other", "Fireball is banned", None, None, None, DM, NOW, NOW)
        self.store.rules.append(other)
        self.store.list = AsyncMock(return_value=[])  # type: ignore[method-assign]
        await self.deliver("house rule: Fireball is a bonus action")
        self.assertNotIn("banned", self.posts[0][1])
        self.store.list.assert_awaited_once_with(GUILD, C1)

    async def test_keep_both_adds_a_new_rule(self) -> None:
        self.store.seed("Fireball burns scrolls")
        await self.deliver("house rule: Fireball is a bonus action")
        it = self.interaction(content=self.posts[0][1])
        await self.button(self.posts[0][2], "Save as new rule").callback(it)
        self.assertEqual(len(self.store.rules), 2)
        self.assertIn("✅ Saved as house rule 2.", it.response.edited[0][0])

    async def test_replace_puts_the_new_words_in_the_old_rule_keeping_its_number(self) -> None:
        self.store.seed("Fireball burns scrolls", "Fireball only damages")
        await self.deliver("house rule: Fireball is a bonus action")
        it = self.interaction(content=self.posts[0][1])
        await self.button(self.posts[0][2], "Replace rule 1").callback(it)
        (rule,) = self.store.rules
        self.assertEqual((rule.number, rule.rule), (1, "Fireball is a bonus action"))
        self.assertEqual(rule.supersedes, "Fireball only damages")  # kept
        self.assertIn("🔁 House rule 1 now says this.", it.response.edited[0][0])
        self.assertIn("It used to say: “Fireball burns scrolls”", it.response.edited[0][0])

    async def test_replace_is_refused_if_another_dm_changed_the_rule_meanwhile(self) -> None:
        self.store.seed("Fireball burns scrolls")
        await self.deliver("house rule: Fireball is a bonus action")
        current = self.store.rules[0]
        self.store.rules[0] = HouseRule(1, C1, "Fireball burns everything", None, None, None, DM,
                                        NOW, NOW, current.version + 1)  # fmt: skip
        it = self.interaction()
        await self.button(self.posts[0][2], "Replace rule 1").callback(it)
        self.assertEqual(it.response.sent[0][0], house.CHANGED)
        self.assertEqual(self.store.rules[0].rule, "Fireball burns everything")

    async def test_replace_of_a_rule_removed_meanwhile_is_told(self) -> None:
        self.store.seed("Fireball burns scrolls")
        await self.deliver("house rule: Fireball is a bonus action")
        self.store.rules.clear()
        it = self.interaction()
        await self.button(self.posts[0][2], "Replace rule 1").callback(it)
        self.assertEqual(it.response.sent[0][0], house.GONE)

    async def test_many_clashes_are_counted(self) -> None:
        for n in range(5):
            self.store.seed(f"Fireball variant {n}")
        await self.deliver("house rule: Fireball is a bonus action")
        text = self.posts[0][1]
        self.assertEqual(text.count("mentions the same thing"), screen.CLASH_SHOWN)
        self.assertIn("…and 3 more", text)
        self.assertEqual(
            [str(b.item.label) for b in self.posts[0][2].children],
            ["Save as new rule", "Edit", "Cancel"],  # no Replace when it is unclear which
        )
        self.assertLessEqual(len(text), 2000)


class TheFile(TableTest):
    """The house-rules file follows a saved proposal (#969)."""

    @staticmethod
    def files(it: Any) -> list[Any]:
        return [c for c in it.followup.send.await_args_list if "file" in c.kwargs]

    async def test_save_sends_the_updated_file_to_the_one_who_pressed(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        it = self.interaction(content=self.posts[0][1])
        await self.button(self.posts[0][2], "Save rule").callback(it)
        (call,) = self.files(it)
        self.assertTrue(call.kwargs["ephemeral"])
        self.assertIn("Saved as house rule 1.", call.args[0])
        self.assertIn("1. Potions are a bonus action", call.kwargs["file"].fp.read().decode())

    async def test_replace_and_the_edit_form_send_it_too(self) -> None:
        self.store.seed("Fireball burns scrolls")
        await self.deliver("house rule: Fireball is a bonus action")
        it = self.interaction(content=self.posts[0][1])
        await self.button(self.posts[0][2], "Replace rule 1").callback(it)
        (call,) = self.files(it)
        self.assertIn("1. Fireball is a bonus action", call.kwargs["file"].fp.read().decode())

    async def test_a_cancel_or_a_refusal_sends_no_file(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        it = self.interaction(PLAYER)
        await self.button(self.posts[0][2], "Save rule").callback(it)
        cancel = self.interaction()
        await self.button(self.posts[0][2], "Cancel").callback(cancel)
        self.assertEqual(self.files(it) + self.files(cancel), [])


class Typed(TableTest):
    async def test_a_typed_rule_is_the_same_proposal_marked_typed(self) -> None:
        started = self.bot.sidebar_house_rule(
            self.table, DM, "house rule: potions are a bonus action"
        )
        await self.settle()
        self.assertEqual(started, "started")
        (post,) = self.posts
        self.assertEqual(post[0], SCREEN)
        self.assertEqual(
            [str(b.item.label) for b in post[2].children], ["Save rule", "Edit", "Cancel"]
        )
        await self.button(post[2], "Save rule").callback(self.interaction(content=post[1]))
        (added,) = self.store.added
        self.assertRegex(added["scenario"], r"^Typed by the DM, \d{4}-\d{2}-\d{2}$")
        self.assertEqual(added["session_id"], "session-1")

    async def test_the_limits_are_shared_with_what_is_said_aloud(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        soon = self.bot.sidebar_house_rule(self.table, DM, "house rule: no flanking")
        self.assertEqual(soon, "too-soon")
        self.rewind()
        repeat = self.bot.sidebar_house_rule(  # the same words once a session, however they came
            self.table, DM, "House rule: potions are a bonus action."
        )
        self.assertEqual(repeat, "repeat")

    async def test_a_player_or_a_finished_session_starts_nothing(self) -> None:
        text = "house rule: potions are a bonus action"
        self.assertEqual(self.bot.sidebar_house_rule(self.table, PLAYER, text), "not-now")
        self.table.listening = False
        self.assertEqual(self.bot.sidebar_house_rule(self.table, DM, text), "not-now")
        self.assertEqual(self.posts, [])


class Races(TableTest):
    async def test_two_presses_of_save_write_one_rule(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        view = self.posts[0][2]
        real = self.store.add

        async def slow(*args: Any, **kwargs: Any) -> HouseRule:
            await asyncio.sleep(0)
            return await real(*args, **kwargs)

        self.store.add = slow  # type: ignore[method-assign]
        first, second = self.interaction(), self.interaction()
        await asyncio.gather(
            self.button(view, "Save rule").callback(first),
            self.button(view, "Save rule").callback(second),
        )
        self.assertEqual(len(self.store.added), 1)
        self.assertEqual(len(first.response.edited) + len(second.response.edited), 1)
        closed = first.response.sent or second.response.sent
        self.assertEqual(closed[0][0], screen.CLOSED)

    async def test_a_refused_save_gives_the_proposal_back(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        self.store.add = AsyncMock(side_effect=HouseRuleError(house.FULL))  # type: ignore[method-assign]
        await self.button(self.posts[0][2], "Save rule").callback(self.interaction())
        self.assertEqual(len(self.table.house_voice.proposals), 1)

    async def test_an_edit_saved_twice_writes_one_rule(self) -> None:
        await self.deliver("house rule: potions are a bonus action")
        (proposal_id,) = self.table.house_voice.proposals
        proposal = self.table.house_voice.proposals[proposal_id]
        forms = [house_ui.ProposalForm(campaign(), proposal, proposal_id) for _ in range(2)]
        submits = [self.interaction(), self.interaction()]
        for form in forms:
            form.rule._value, form.instead._value = "Potions are free", ""
        for form, submit in zip(forms, submits, strict=True):
            await form.on_submit(submit)
        self.assertEqual(len(self.store.added), 1)
        self.assertEqual(submits[1].response.sent[0][0], screen.CLOSED)
        self.assertEqual(self.table.house_voice.proposals, {})


class Trouble(TableTest):
    async def test_unreadable_house_rules_are_said_not_hidden(self) -> None:
        self.store.list = AsyncMock(side_effect=TimeoutError)  # type: ignore[method-assign]
        await self.deliver("house rule: potions are a bonus action")
        self.assertIn(screen.UNCHECKED, self.posts[0][1])

    async def test_a_post_that_raises_gives_back_the_words(self) -> None:
        async def broken(channel_id: int, text: str, view: Any = None) -> None:
            raise RuntimeError("boom")

        self.bot.post_message = broken  # type: ignore[method-assign]
        await self.deliver("house rule: potions are a bonus action")
        self.assertEqual(
            (self.table.house_voice.seen, self.table.house_voice.proposals), (set(), {})
        )


class Registration(unittest.TestCase):
    def test_every_proposal_button_is_registered_for_after_a_restart(self) -> None:
        tree = ast.parse(inspect.getsource(bot_module))
        registered = {
            arg.attr if isinstance(arg, ast.Attribute) else arg.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_dynamic_items"
            for arg in node.args
            if isinstance(arg, (ast.Name, ast.Attribute))
        }
        proposals = [
            Proposal(Said("x y", False)),
            Proposal(Said("x y", False), (Clash(1, 1, "w"),)),
        ]
        built = {
            type(item).__name__
            for proposal in proposals
            for item in screen.proposal_view(GUILD, "abcdef01", proposal).children
        }
        self.assertEqual(built, {"HouseVoiceButton"})
        self.assertLessEqual(built, registered)


class Words(unittest.TestCase):
    def test_the_words_are_plain(self) -> None:
        for text in (screen.CLOSED, screen.ONLY_DMS, screen.NOT_SAVED):
            self.assertNotRegex(text, r"(?i)\b(srd|ruleset|index|query|database|scenario)\b")
        self.assertIn("/dmbot houserules", screen.CLOSED)  # what to do next
        self.assertIn("Everyone in the server can read", screen.NOT_SAVED)  # who sees it
        self.assertEqual(screen.scenario_for(time.mktime((2026, 10, 9, 12, 0, 0, 0, 0, 0))),
                         "Said at the table, 2026-10-09")  # fmt: skip


from dmbot.rules import house_voice as house_voice_module  # noqa: E402

if __name__ == "__main__":
    unittest.main()
