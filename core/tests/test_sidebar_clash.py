"""Two house rules that disagree, met while answering (#2284, step 3): a settled pair is followed
without a question, a clash the answer names is checked in code, the DM is asked and nothing
is picked, and a player's table question gets "one for your DM" while the DM is told."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.rules.followed import FollowedRule
from dmbot.rules.house import HouseRule
from dmbot.rules.wins import RuleRef, Win
from dmbot.sidebar import rule_clash
from dmbot.sidebar.answer import Sidebar
from dmbot.sidebar.service import SidebarService
from dmbot.ui.rule_clash import ClashView
from tests.test_sidebar_answer import INDEX, FakeAI, campaign, engine, house
from tests.test_sidebar_service import (
    DM,
    GUILD,
    PLAYER,
    FakeAnswer,
    FakeAnswerer,
    FakeHost,
    make_table,
)

POTION_A = "Drinking a potion is a bonus action"
POTION_B = "Drinking a potion is an item interaction"
ASK = "can I drink a potion as a bonus action?"


def followed(number: int, rule: str, set_name: str = "Neighbours") -> FollowedRule:
    return FollowedRule(number, "c" * 32, rule, None, None, None, 7, 0, 0, set_name=set_name)


def reply_with(conflict: str, answer: str = "clash") -> str:
    return f"ANSWER: {answer}\nSOURCE: none\nCONFIDENCE: uncertain\nCONFLICT: {conflict}"


class Pure(unittest.TestCase):
    def test_a_settled_pair_leaves_out_only_the_loser(self) -> None:
        a, b, c = house(1, POTION_A), house(2, POTION_B), house(3, "Falling hurts")
        win = Win(RuleRef("", 1), RuleRef("", 2), 7, 0)
        self.assertEqual(rule_clash.settled([a, b, c], [win]), [a, c])
        self.assertEqual(rule_clash.settled([b, c], [win]), [b, c])  # the winner is not shown

    def test_a_named_pair_must_be_two_different_rules_it_was_shown(self) -> None:
        shown = [house(1, POTION_A), house(2, POTION_B), followed(2, "Potions are free")]
        got = rule_clash.named("House rule 1, House rule 2", shown)
        assert got is not None
        self.assertEqual([s.label for s in got], ["House rule 1", "House rule 2"])
        self.assertEqual(got[0].ref, RuleRef("", 1))
        pair = rule_clash.named(
            "Shared rule 2, Shared rule 3", [followed(2, "A"), followed(3, "B", "Other")]
        )
        assert pair is not None
        self.assertEqual([s.ref for s in pair], [RuleRef("Neighbours", 2), RuleRef("Other", 3)])

    def test_the_campaigns_own_rule_outranks_a_followed_one_so_that_is_not_a_question(self) -> None:
        shown = [house(1, POTION_A), followed(2, POTION_B)]
        self.assertIsNone(rule_clash.named("House rule 1, Shared rule 2", shown))

    def test_a_chain_drops_only_the_middle_rule_and_a_cycle_drops_none(self) -> None:
        a, b, c = house(1, "A"), house(2, "B"), house(3, "C")
        chain = [
            Win(RuleRef("", 1), RuleRef("", 2), 7, 0),
            Win(RuleRef("", 2), RuleRef("", 3), 7, 0),
        ]
        self.assertEqual(rule_clash.settled([a, b, c], chain), [a, c])
        cycle = [*chain, Win(RuleRef("", 3), RuleRef("", 1), 7, 0)]
        self.assertEqual(rule_clash.settled([a, b, c], cycle), [a, b, c])

    def test_the_rules_words_are_escaped_for_the_dms_message(self) -> None:
        sides = rule_clash.named(
            "House rule 1, House rule 2",
            [house(1, "A *potion* @everyone [x](http://e.example)"), house(2, "B")],
        )
        assert sides is not None
        self.assertNotIn("@everyone", sides[0].words.replace("@\u200beveryone", ""))
        self.assertIn("\\*potion\\*", sides[0].words)

    def test_anything_else_is_dropped(self) -> None:
        shown = [house(1, POTION_A), house(2, POTION_B)]
        for bad in (
            "none", "", "House rule 1", "House rule 1, House rule 1", "House rule 1, House rule 9",
            "Shared rule 1, House rule 2", "the potion ones", "rule 1 and rule 2",
        ):  # fmt: skip
            self.assertIsNone(rule_clash.named(bad, shown), bad)

    def test_two_rules_with_one_label_are_not_a_name(self) -> None:
        shown = [followed(1, "A", "One"), followed(1, "B", "Two"), house(2, POTION_B)]
        self.assertIsNone(rule_clash.named("Shared rule 1, House rule 2", shown))


class TheEngine(unittest.IsolatedAsyncioTestCase):
    def build(
        self, replies: list[str], rules: list[HouseRule], wins: list[Win] | None = None
    ) -> Any:
        ai = FakeAI(replies)
        eng = engine(ai, houses={"c" * 32: rules})

        async def settled(_campaign: Any) -> list[Win]:
            return wins or []

        eng._settled = settled
        return eng, ai

    async def test_a_named_clash_asks_the_dm_quoting_both_and_picks_neither(self) -> None:
        eng, ai = self.build(
            [reply_with("House rule 1, House rule 2")], [house(1, POTION_A), house(2, POTION_B)]
        )
        got = await eng.answer(campaign(), ASK)
        assert got.clash is not None
        self.assertIn("Which one applies?", got.text)
        self.assertIn(POTION_A, got.text)
        self.assertIn(POTION_B, got.text)
        self.assertIn("House rule 1", got.text)
        self.assertEqual(len(ai.prompts), 1)  # no extra AI call
        self.assertIn("CONFLICT", ai.systems[0])

    async def test_a_table_question_gets_the_fixed_line_and_the_rules_stay_out_of_it(self) -> None:
        eng, _ = self.build(
            [reply_with("House rule 1, House rule 2")], [house(1, POTION_A), house(2, POTION_B)]
        )
        got = await eng.answer(campaign(), ASK, asker_id=8, table=True)
        self.assertEqual(got.text, "That's one for your DM.")
        self.assertTrue(got.one_for_dm)
        self.assertNotIn("potion", " ".join(got.parts).lower())
        assert got.clash is not None

    async def test_a_bad_conflict_line_is_dropped_and_the_answer_goes_on(self) -> None:
        eng, _ = self.build(
            [reply_with("House rule 1, House rule 7", answer="Yes.")],
            [house(1, POTION_A), house(2, POTION_B)],
        )
        got = await eng.answer(campaign(), ASK)
        self.assertIsNone(got.clash)
        self.assertNotIn("Which one applies?", got.text)

    async def test_a_settled_pair_is_followed_with_no_question_from_the_winner_only(self) -> None:
        win = Win(RuleRef("", 1), RuleRef("", 2), 7, 0)
        eng, ai = self.build(
            ["ANSWER: Yes, a bonus action.\nSOURCE: House rule 1\nCONFIDENCE: confident"],
            [house(1, POTION_A), house(2, POTION_B)],
            [win],
        )
        got = await eng.answer(campaign(), ASK)
        self.assertIsNone(got.clash)
        self.assertIn(POTION_A, ai.prompts[0])
        self.assertNotIn(POTION_B, ai.prompts[0])
        self.assertNotIn("CONFLICT", ai.systems[0])  # one rule left: nothing to clash

    async def test_one_rule_alone_never_gets_the_clash_wording(self) -> None:
        eng, ai = self.build(
            ["ANSWER: Yes.\nSOURCE: House rule 1\nCONFIDENCE: confident"], [house(1, POTION_A)]
        )
        await eng.answer(campaign(), ASK)
        self.assertNotIn("CONFLICT", ai.systems[0])


class MoreEngine(unittest.IsolatedAsyncioTestCase):
    async def test_the_word_clash_is_never_shown_when_its_line_does_not_check_out(self) -> None:
        for conflict in ("none", "House rule 1, House rule 9", ""):
            ai = FakeAI([reply_with(conflict)])
            got = await engine(
                ai, houses={"c" * 32: [house(1, POTION_A), house(2, POTION_B)]}
            ).answer(campaign(), ASK)
            self.assertIsNone(got.clash)
            self.assertNotIn("clash", got.text.lower().replace("which one applies", ""))
            table = await engine(
                FakeAI([reply_with(conflict)]),
                houses={"c" * 32: [house(1, POTION_A), house(2, POTION_B)]},
            ).answer(campaign(), ASK, asker_id=8, table=True)
            self.assertEqual(table.text, "That's one for your DM.")

    async def test_only_a_rule_the_prompt_showed_can_be_named(self) -> None:
        rules = [house(n, f"Potion rule number {n} about drinking a potion") for n in range(1, 9)]
        ai = FakeAI([reply_with("House rule 1, House rule 8")])
        got = await engine(ai, houses={"c" * 32: rules}).answer(campaign(), ASK)
        shown = {int(n) for n in __import__("re").findall(r"House rule (\d+):", ai.prompts[0])}
        self.assertLessEqual(len(shown), 5)
        self.assertEqual(got.clash is not None, {1, 8} <= shown)

    async def test_the_clash_answer_keeps_no_sources_and_the_settled_list_is_wired_in(self) -> None:
        win = Win(RuleRef("", 1), RuleRef("", 2), 7, 0)
        ai = FakeAI(["ANSWER: Yes.\nSOURCE: House rule 1\nCONFIDENCE: confident"])

        async def settled(_c: Any) -> list[Win]:
            return [win]

        async def gate(c: Any, user: int) -> None:
            return None

        async def houses(c: Any) -> list[HouseRule]:
            return [house(1, POTION_A), house(2, POTION_B)]

        async def names(c: Any) -> None:
            return None

        built = Sidebar(ai, INDEX, gate=gate, houses=houses, names=names, settled=settled)  # type: ignore[arg-type]
        await built.answer(campaign(), ASK, asker_id=7)
        self.assertNotIn(POTION_B, ai.prompts[0])
        both = await engine(
            FakeAI([reply_with("House rule 1, House rule 2")]),
            houses={"c" * 32: [house(1, POTION_A), house(2, POTION_B)]},
        ).answer(campaign(), ASK)
        self.assertEqual(both.sources, ())


class TheBot(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_who_lost_the_role_since_the_question_cannot_record_a_win(self) -> None:
        from dmbot.bot import DMBot

        wins = SimpleNamespace(record=AsyncMock())
        now = SimpleNamespace(dm_user_ids=frozenset({70}))  # DM (7) is no longer one
        fake = SimpleNamespace(
            house_wins=wins, campaigns=SimpleNamespace(get=AsyncMock(return_value=now))
        )
        stale = SimpleNamespace(guild_id=GUILD, id="c", dm_user_ids=frozenset({DM}))
        record = DMBot.sidebar_record_win.__get__(fake)
        self.assertFalse(await record(stale, RuleRef("", 1), RuleRef("", 2), DM))
        wins.record.assert_not_awaited()
        self.assertTrue(await record(stale, RuleRef("", 1), RuleRef("", 2), 70))
        wins.record.assert_awaited_once()


class TheButtons(unittest.IsolatedAsyncioTestCase):
    def view(self) -> tuple[ClashView, Any, Any]:
        sides = rule_clash.named(
            "House rule 1, House rule 2", [house(1, POTION_A), house(2, POTION_B)]
        )
        assert sides is not None
        record, retry = AsyncMock(return_value=True), AsyncMock()
        return ClashView(sides, dm_ids={DM}, record=record, retry=retry), record, retry

    def press(self, user: int) -> Any:
        it = MagicMock()
        it.user.id = user
        it.response.send_message = AsyncMock()
        it.response.edit_message = AsyncMock()
        it.response.defer = AsyncMock()
        return it

    async def test_one_button_for_each_rule_and_a_player_cannot_press(self) -> None:
        view, record, _retry = self.view()
        self.assertEqual(
            [getattr(c, "label", "") for c in view.children],
            ["House rule 1 applies", "House rule 2 applies"],
        )
        player = self.press(PLAYER)
        self.assertFalse(await view.interaction_check(player))
        record.assert_not_awaited()
        self.assertTrue(await view.interaction_check(self.press(DM)))

    async def test_the_press_records_the_winner_and_asks_again_once(self) -> None:
        view, record, retry = self.view()
        it = self.press(DM)
        await view.children[1].callback(it)  # type: ignore[attr-defined]
        record.assert_awaited_once_with(RuleRef("", 2), RuleRef("", 1), DM)
        retry.assert_awaited_once()
        await view.children[0].callback(self.press(DM))  # type: ignore[attr-defined]
        record.assert_awaited_once()  # a second press changes nothing
        retry.assert_awaited_once()

    async def test_a_retry_that_fails_says_so_instead_of_staying_on_asking_again(self) -> None:
        view, _record, retry = self.view()
        retry.side_effect = RuntimeError("boom")
        it = self.press(DM)
        it.edit_original_response = AsyncMock()
        await view.children[0].callback(it)  # type: ignore[attr-defined]
        self.assertIn("couldn't answer", it.edit_original_response.await_args.kwargs["content"])

    async def test_unanswered_buttons_are_taken_away_when_they_time_out(self) -> None:
        view, record, _retry = self.view()
        view._message = view.message = MagicMock()  # type: ignore[assignment]
        view.message.edit = AsyncMock()  # type: ignore[union-attr]
        await view.on_timeout()
        self.assertIn("neither rule was used", view.message.edit.await_args.kwargs["content"])  # type: ignore[union-attr]
        self.assertIsNone(view.message.edit.await_args.kwargs["view"])  # type: ignore[union-attr]
        record.assert_not_awaited()

    async def test_a_choice_that_could_not_be_saved_does_not_ask_again(self) -> None:
        view, record, retry = self.view()
        record.return_value = False
        await view.children[0].callback(self.press(DM))  # type: ignore[attr-defined]
        retry.assert_not_awaited()


class AtTheTable(unittest.IsolatedAsyncioTestCase):
    CHAT = 555

    def setUp(self) -> None:
        self.table = make_table(chat=self.CHAT)
        self.host = FakeHost(tables={GUILD: self.table}, agreed={(GUILD, DM), (GUILD, PLAYER)})
        self.recorded: list[tuple[Any, Any]] = []

        async def campaign_of(guild_id: int, campaign_id: str) -> Any:
            return SimpleNamespace(id=campaign_id, guild_id=guild_id, dm_user_ids=frozenset({DM}))

        async def record(_campaign: Any, winner: Any, loser: Any, by: int) -> bool:
            self.recorded.append((winner, loser))
            return True

        self.host.sidebar_campaign = campaign_of  # type: ignore[method-assign]
        self.host.sidebar_record_win = record  # type: ignore[method-assign]
        sides = rule_clash.named(
            "House rule 1, House rule 2", [house(1, POTION_A), house(2, POTION_B)]
        )
        self.clash = FakeAnswer(
            text=rule_clash.question_text(*sides),  # type: ignore[misc]
            in_game=False,
        )
        self.clash.clash = sides  # type: ignore[attr-defined]
        self.answerer = FakeAnswerer(self.clash)
        self.sidebar = SidebarService(self.host)  # type: ignore[arg-type]
        self.sidebar.answerer = self.answerer  # type: ignore[assignment]
        self.now = [1000.0]
        self.sidebar._clock = lambda: self.now[0]

    async def settle(self) -> None:
        await asyncio.gather(*self.sidebar._tasks)

    async def test_the_dms_table_chat_question_goes_to_them_alone_with_the_buttons(self) -> None:
        sides = self.clash.clash
        self.answerer.answer_to_give = FakeAnswer(
            text="That's one for your DM.", one_for_dm=True, clash=sides
        )
        self.sidebar.ask_in_chat(self.table, DM, "can I drink a potion as a bonus action?")
        await self.settle()
        self.assertEqual(self.answerer.table_modes, [True])  # no second call that could skip it
        self.assertTrue(any("Which one applies?" in t for _u, t in self.host.dms), self.host.dms)
        self.assertFalse(any("Which one applies?" in t for t in self.host.chat))
        self.assertIsNotNone(self.host.views[-1])  # the buttons are under it

    async def test_the_dms_private_question_gets_the_buttons_and_the_transcript_keeps_no_rules(
        self,
    ) -> None:
        reply = AsyncMock(return_value=True)
        await self.sidebar._ask(self.table, DM, "can I drink a potion?", reply, via="typed")
        text, view = reply.await_args.args
        self.assertIn("Which one applies?", text)
        self.assertEqual(len(view.children), 2)
        saved = " ".join(
            str(line.sidebar) + str(getattr(line, "text", "")) for line in self.host.saved
        )
        self.assertNotIn("item interaction", saved)

    async def test_a_players_table_question_tells_the_dm_which_two_and_nothing_else(self) -> None:
        public = FakeAnswer(text="That's one for your DM.", one_for_dm=True)
        public.clash = self.clash.clash  # type: ignore[attr-defined]
        self.answerer.answer_to_give = public
        self.sidebar.ask_in_chat(self.table, PLAYER, "can I drink a potion as a bonus action?")
        await self.settle()
        self.assertTrue(any("That's one for your DM." in c for c in self.host.chat))
        self.assertEqual(len(self.host.screen), 1)
        self.assertIn("House rule 1", self.host.screen[0])
        self.assertNotIn("item interaction", self.host.screen[0].lower())
        self.assertNotIn("item interaction", " ".join(self.host.chat).lower())  # no rule's words


if __name__ == "__main__":
    unittest.main()
