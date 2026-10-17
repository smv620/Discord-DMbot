"""How the table runs initiative, heard (#1771): the strict phrases and their near-misses, who
counts (a DM sets it; two different players can, until a DM has), what changes when FightBot
speaks for each style, and the DM's combat line. No AI and no Discord; FightBot's own
behaviour with no style heard is pinned by the existing tests in test_fightbot.py."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot import fightbot
from dmbot.dm_screen import clock as clock_ui
from dmbot.fight_style import StyleState, hear
from dmbot.memory import fight_prompt
from dmbot.memory.sheet_store import CharacterSheet
from dmbot.timebot.clock import Clock
from tests import test_fightbot as tf
from tests.test_sheet_reread import base
from tests.test_sheet_store import PLAYER

DM = 5
NAMES = {"mira": 1, "kael": 2, "thorn": 3, "belleros": PLAYER}
YES = lambda user: True  # noqa: E731


class Phrases(unittest.TestCase):
    def heard(self, line: str, round_number: int | None = 1) -> Any:
        return hear(line, NAMES, round_number=round_number)

    def test_one_creature_at_a_time(self) -> None:
        for line, user in (
            ("Mira, you're up", 1),
            ("Thorn, your turn", 3),
            ("Kael, it's your turn.", 2),
            ("Mira's turn", 1),
        ):
            got = self.heard(line)
            self.assertEqual((got.order, got.turn), ("each", (user,)), line)
        for line, user in (("Mira, you're on deck", 1), ("Kael, you're next", 2)):
            got = self.heard(line)
            self.assertEqual((got.order, got.on_deck, got.turn), ("each", (user,), ()), line)
        got = self.heard("goblin two, its turn")
        self.assertEqual((got.order, got.creature, got.turn), ("each", "goblin 2", ()))

    def test_a_side_acting_together(self) -> None:
        for line in ("players go first", "your side goes", "the party acts", "Party goes first"):
            got = self.heard(line)
            self.assertEqual((got.order, got.players_side), ("side", True), line)
        for line in ("monsters' turn", "the enemies go", "the goblins' turn"):
            got = self.heard(line)
            self.assertEqual((got.order, got.players_side), ("side", False), line)
        for line in ("side initiative", "group initiative", "we use side based initiative"):
            self.assertEqual(self.heard(line).order, "side", line)

    def test_the_goblins_turn_is_a_side_but_goblin_two_is_one_creature(self) -> None:
        self.assertEqual(self.heard("the goblins' turn").order, "side")
        self.assertEqual(self.heard("goblin two, its turn").order, "each")

    def test_initiative_rolled_again_in_a_running_fight(self) -> None:
        for line in ("reroll initiative", "everyone roll again", "we roll initiative every round"):
            got = self.heard(line, round_number=1)
            self.assertEqual((got.order, got.reroll_call), ("reroll", True), line)
        again = self.heard("roll initiative", round_number=2)
        self.assertEqual((again.order, again.reroll_call), ("reroll", True))
        self.assertIsNone(self.heard("roll initiative", round_number=1))  # that starts the fight
        self.assertIsNone(self.heard("roll initiative", round_number=None))

    def test_asking_for_declarations_even_as_a_question(self) -> None:
        for line in (
            "declare your actions",
            "everyone say what you're doing",
            "what's everyone doing this round?",
            "declarations",
        ):
            got = self.heard(line)
            self.assertEqual((got.declarations, got.declare_call), ("yes", True), line)

    def test_a_players_roll_points_to_each_or_reroll_never_to_side(self) -> None:
        for line in ("I got a 17", "seventeen for initiative", "17 for initiative"):
            got = self.heard(line)
            self.assertEqual((got.rolled, got.order), (True, None), line)

    def test_near_misses_are_not_heard(self) -> None:
        for line in (
            "he'll roll initiative when the door opens",
            "is it my turn?",
            "the goblin goes next round",
            "if the players go first you win",
            "I think the party acts now",
            "maybe side initiative",
            "it's your turn",
            "the goblins",
            "what are you doing",
            "",
        ):
            got = self.heard(line)
            self.assertTrue(got is None or (got.order is None and not got.turn), line)
        self.assertIsNone(self.heard("is it my turn?"))
        self.assertIsNone(self.heard("the goblin goes next round"))
        self.assertIsNone(self.heard("he'll roll initiative when the door opens"))
        self.assertIsNone(self.heard("a long story about a very long fight that goes on and on"))

    def test_a_turn_called_for_a_name_nobody_has_says_the_style_but_moves_no_one(self) -> None:
        got = self.heard("Bob, you're up")
        self.assertEqual((got.order, got.turn, got.creature), ("each", (), None))

    def test_curly_apostrophes_are_the_same(self) -> None:
        self.assertEqual(self.heard("Mira, you’re up").turn, (1,))
        self.assertEqual(self.heard("the goblins’ turn").order, "side")


class WhoCounts(unittest.TestCase):
    def test_a_dm_line_sets_the_style(self) -> None:
        state = StyleState()
        self.assertTrue(state.vote("order", "side", DM, from_dm=True, consenting=YES))
        self.assertEqual((state.order, state.set_by["order"]), ("side", "dm"))
        self.assertTrue(state.known)

    def test_one_player_alone_sets_nothing_two_different_players_do(self) -> None:
        state = StyleState()
        self.assertFalse(state.vote("order", "each", 1, from_dm=False, consenting=YES))
        self.assertFalse(state.vote("order", "each", 1, from_dm=False, consenting=YES))  # same one
        self.assertEqual(state.order, "unknown")
        self.assertTrue(state.vote("order", "each", 2, from_dm=False, consenting=YES))
        self.assertEqual((state.order, state.set_by["order"]), ("each", "players"))

    def test_a_dm_overrides_two_players_and_players_cannot_overrule_a_dm(self) -> None:
        state = StyleState()
        state.vote("order", "each", 1, from_dm=False, consenting=YES)
        state.vote("order", "each", 2, from_dm=False, consenting=YES)
        self.assertEqual(state.order, "each")
        state.vote("order", "side", DM, from_dm=True, consenting=YES)
        self.assertEqual(state.order, "side")
        for user in (1, 2, 3):
            state.vote("order", "each", user, from_dm=False, consenting=YES)
        self.assertEqual(state.order, "side")  # players' lines no longer count

    def test_someone_who_withdrew_consent_no_longer_counts(self) -> None:
        state = StyleState()
        state.vote("order", "each", 1, from_dm=False, consenting=YES)
        # the first voter withdraws before the second speaks
        self.assertFalse(state.vote("order", "each", 2, from_dm=False, consenting=lambda u: u != 1))
        self.assertEqual(state.order, "unknown")
        # and a style two players made is forgotten when one of them withdraws
        state = StyleState()
        state.vote("order", "each", 1, from_dm=False, consenting=YES)
        state.vote("order", "each", 2, from_dm=False, consenting=YES)
        self.assertTrue(state.prune(lambda u: u != 2))
        self.assertEqual(state.order, "unknown")
        # a DM's word stays
        state.vote("order", "each", DM, from_dm=True, consenting=YES)
        self.assertFalse(state.prune(lambda u: False))
        self.assertEqual(state.order, "each")

    def test_the_two_things_are_separate(self) -> None:
        state = StyleState()
        state.vote("declarations", "yes", DM, from_dm=True, consenting=YES)
        self.assertEqual((state.order, state.declarations), ("unknown", "yes"))
        self.assertTrue(state.known)
        state = StyleState()
        self.assertTrue(state.infer_no_declarations())
        self.assertFalse(state.known)  # "no declarations" alone changes nothing
        self.assertTrue(state.forget_inferred())
        self.assertEqual(state.declarations, "unknown")


class Phrase(unittest.TestCase):
    def test_the_dm_line_for_each_style(self) -> None:
        s = StyleState()
        self.assertEqual(s.dm_phrase(), "Initiative, as heard: not heard yet")
        for order, words in (
            ("each", "one at a time"),
            ("side", "by side"),
            ("reroll", "rolled each round"),
        ):
            s.order = order  # type: ignore[assignment]
            self.assertEqual(s.dm_phrase(), f"Initiative, as heard: {words}")
        s.declarations = "yes"
        self.assertEqual(
            s.dm_phrase(), "Initiative, as heard: rolled each round; actions declared first"
        )
        s.order = "unknown"
        self.assertEqual(s.dm_phrase(), "Initiative, as heard: actions declared first")

    def test_only_the_dms_clock_line_in_a_fight_shows_it(self) -> None:
        fighting = Clock(0, 0, None, 2, 0)
        text = clock_ui.clock_text(fighting, None, None, "Initiative, as heard: by side")
        self.assertIn("⚔️ Combat, round 2\nInitiative, as heard: by side", text)
        calm = clock_ui.clock_text(Clock(0, 0), None, None, "Initiative, as heard: by side")
        self.assertNotIn("Initiative", calm)
        self.assertNotIn("Initiative", clock_ui.players_text(fighting))  # players never see it


def sheets_for(users: list[int]) -> list[CharacterSheet]:
    return [
        CharacterSheet(f"x{u}", f"Hero{u}", u, None, {**base(), "spells": ["Hunter's Mark"]}, 1)
        for u in users
    ]


class Timed(unittest.IsolatedAsyncioTestCase):
    """FightBot's tick for each heard style. The harness is test_fightbot's Runner."""

    setUp = tf.Runner.setUp
    table = tf.Runner.table
    bot = tf.Runner.bot

    def primed(self, users: list[int], round_number: int = 1) -> tuple[Any, Any]:
        table = self.table()
        table.dm_user_ids = {DM}
        bot = self.bot(
            table,
            round_number=round_number,
            sheets_=sheets_for(users),
            in_voice=(DM, *users),
        )
        fight = table.fight
        fight.secrets = []
        fight.round = round_number
        fight.present = set(users) | {DM}
        fight.names = {f"hero{u}": u for u in users}
        return table, bot

    def dm_says(self, table: Any, line: str) -> bool:
        return fightbot.note_line(table, DM, line, is_dm=True, consenting=YES)

    def player_says(self, table: Any, user: int, line: str) -> bool:
        return fightbot.note_line(table, user, line, is_dm=False, consenting=YES)

    async def tick(self, bot: Any, table: Any) -> None:
        await fightbot.tick(bot, table)
        fight = table.fight
        # (the pass refreshes names and secrets; keep the primed ones for the next line)
        fight.names = {f"hero{u}": u for u in fight.present if u != DM}

    def calls(self, bot: Any) -> int:
        return int(bot.fight_ai.complete.await_count)

    def last_prompt(self, bot: Any) -> str:
        return str(bot.fight_ai.complete.await_args.args[1])

    # ---- each ------------------------------------------------------------------------

    async def test_each_learns_the_order_in_round_one_and_reminds_the_next_player_in_round_two(
        self,
    ) -> None:
        table, bot = self.primed([11, 12])
        for line in ("goblin one, its turn", "Hero11, you're up"):
            self.dm_says(table, line)
        self.assertEqual(table.fight.style.order, "each")
        await self.tick(bot, table)  # the player just placed is reminded; nobody else is due
        self.assertEqual(self.calls(bot), 1)
        self.assertIn("Right now: It is your turn next", self.last_prompt(bot))
        self.dm_says(table, "goblin two, its turn")
        self.assertEqual(table.fight.order_seen, [("m", "goblin 1"), ("p", 11), ("m", "goblin 2")])
        self.now += 130
        bot.clocks.get = AsyncMock(
            return_value=SimpleNamespace(clock=Clock(0, 0, None, 2, 0))
        )  # round 2
        await self.tick(bot, table)  # the new round ends round one's moments
        before = self.calls(bot)
        self.dm_says(table, "goblin one, its turn")  # the player after it is Hero11
        self.assertIn(11, table.fight.due)
        self.assertEqual(table.fight.due[11].kind, "next")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), before + 1)
        self.assertIn("It is your turn next", self.last_prompt(bot))

    async def test_on_deck_reminds_that_player_then(self) -> None:
        table, bot = self.primed([11, 12])
        self.dm_says(table, "Hero12, you're on deck")
        self.assertEqual(set(table.fight.due), {12})
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 1)
        self.assertEqual(bot.user.send.await_count, 1)

    async def test_only_the_player_with_a_moment_is_reminded_once_a_style_is_heard(self) -> None:
        table, bot = self.primed([11, 12])
        self.dm_says(table, "Hero12, you're on deck")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 1)  # not both: Hero11 has nothing about to happen
        self.now += 130
        await self.tick(bot, table)  # the timer alone says nothing once a style is heard
        self.assertEqual(self.calls(bot), 1)

    async def test_a_held_reminder_is_dropped_once_the_turn_moved_on(self) -> None:
        table, bot = self.primed([11, 12])
        players = {11: tf.FightPlayer(last_call_at=int(self.now) - 10)}  # called 10 s ago
        bot.sheets.fight_players = AsyncMock(side_effect=lambda g, c: dict(players))
        self.dm_says(table, "Hero11, you're on deck")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 0)  # the 2-minute cap holds it back
        self.assertIn(11, table.fight.due)  # and it waits
        self.dm_says(table, "goblin one, its turn")  # the turn moved on
        self.now += 130
        players[11] = tf.FightPlayer(last_call_at=int(self.now) - 200)
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 0)  # dropped, not sent late
        self.assertNotIn(11, table.fight.due)

    # ---- side ------------------------------------------------------------------------

    async def test_side_reminds_the_present_players_a_few_at_a_time(self) -> None:
        users = [11, 12, 13, 14]
        table, bot = self.primed(users)
        self.dm_says(table, "players go first")
        self.assertEqual(table.fight.style.order, "side")
        self.assertEqual(set(table.fight.due), set(users))
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), fightbot.MAX_CALLS_PER_TICK)
        self.assertIn("Your side acts now", self.last_prompt(bot))
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 2 * fightbot.MAX_CALLS_PER_TICK)

    async def test_the_monsters_turn_says_the_style_but_reminds_nobody(self) -> None:
        table, bot = self.primed([11])
        self.dm_says(table, "the goblins' turn")
        self.assertEqual(table.fight.style.order, "side")
        self.assertEqual(table.fight.due, {})
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 0)

    # ---- reroll and declarations -----------------------------------------------------

    async def test_reroll_reminds_everyone_when_initiative_is_called_again(self) -> None:
        table, bot = self.primed([11, 12], round_number=2)
        self.dm_says(table, "roll initiative")
        self.assertEqual(table.fight.style.order, "reroll")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 2)
        self.assertIn("A new round of initiative is starting", self.last_prompt(bot))

    async def test_a_call_for_declarations_reminds_everyone_and_the_cap_still_holds(self) -> None:
        table, bot = self.primed([11, 12])
        self.dm_says(table, "what's everyone doing this round?")
        self.assertEqual(table.fight.style.declarations, "yes")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 2)
        self.assertIn("The DM is asking what everyone will do this round", self.last_prompt(bot))
        self.now += 20  # asked again 20 seconds later: inside the two-minute gap
        players = {u: tf.FightPlayer(last_call_at=int(self.now) - 20) for u in (11, 12)}
        bot.sheets.fight_players = AsyncMock(side_effect=lambda g, c: dict(players))
        self.dm_says(table, "declare your actions")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 2)  # nobody is reminded twice in two minutes

    async def test_a_round_with_turns_and_no_call_for_declarations_means_none(self) -> None:
        table, bot = self.primed([11])
        self.dm_says(table, "goblin one, its turn")
        self.assertEqual(table.fight.style.declarations, "unknown")
        self.now += 30
        bot.clocks.get = AsyncMock(return_value=SimpleNamespace(clock=Clock(0, 0, None, 2, 0)))
        await self.tick(bot, table)
        self.assertEqual(table.fight.style.declarations, "no")
        self.assertEqual(table.fight.style.set_by["declarations"], "inferred")
        self.dm_says(table, "declare your actions")  # a DM can still change it
        self.assertEqual(table.fight.style.declarations, "yes")

    async def test_the_style_stays_for_the_next_fight_but_what_was_only_inferred_does_not(
        self,
    ) -> None:
        table, bot = self.primed([11])
        self.dm_says(table, "players go first")
        table.fight.style.infer_no_declarations()
        bot.clocks.get = AsyncMock(return_value=SimpleNamespace(clock=Clock(0, 0)))  # fight over
        await self.tick(bot, table)
        self.assertEqual(table.fight.style.order, "side")
        self.assertEqual(table.fight.style.declarations, "unknown")
        self.assertEqual((table.fight.due, table.fight.order_seen), ({}, []))

    # ---- who counts, what is left out ------------------------------------------------

    async def test_a_players_words_never_move_a_turn_or_call_a_moment(self) -> None:
        table, bot = self.primed([11, 12])
        for user in (11, 12):
            self.player_says(table, user, "Hero12, you're up")
            self.player_says(table, user, "players go first")
            self.player_says(table, user, "declare your actions")
        self.assertEqual(table.fight.due, {})
        self.assertEqual((table.fight.turn_no, table.fight.round), (0, 1))
        self.assertEqual(table.fight.order_seen, [])
        self.assertEqual(table.fight.style.declarations, "yes")  # two players agreed on a style...
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 0)  # ...but they called nothing

    async def test_a_line_with_a_secret_name_is_skipped(self) -> None:
        table, _bot = self.primed([11])
        table.fight.secrets = ["Zargothrax"]
        self.assertFalse(self.dm_says(table, "Zargothrax, you're up"))
        self.assertFalse(self.dm_says(table, "players go first, zargothrax"))
        self.assertEqual(table.fight.style.order, "unknown")
        table.fight.secrets = None  # the secret names can't be known: nothing is heard
        self.dm_says(table, "players go first")
        self.assertEqual(table.fight.style.order, "unknown")

    async def test_a_withdrawn_consent_is_dropped_at_the_next_pass(self) -> None:
        table, bot = self.primed([11, 12])
        self.player_says(table, 11, "side initiative")
        self.player_says(table, 12, "side initiative")
        self.assertEqual(table.fight.style.order, "side")
        bot.consent = SimpleNamespace(has_consent=lambda g, u: u != 12)
        await self.tick(bot, table)
        self.assertEqual(table.fight.style.order, "unknown")

    async def test_an_unknown_style_is_exactly_what_fightbot_always_did(self) -> None:
        table, bot = self.primed([11, 12])
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 2)  # both, with no moment line
        self.assertNotIn("Right now", self.last_prompt(bot))

    async def test_a_change_of_style_tells_the_dm_screen_to_refresh(self) -> None:
        table, bot = self.primed([11])
        bot.fight_style_changed = AsyncMock()
        bot.clocks.get = AsyncMock(return_value=SimpleNamespace(clock=Clock(0, 0)))  # fight over
        self.dm_says(table, "players go first")
        table.fight.style.infer_no_declarations()
        await self.tick(bot, table)
        bot.fight_style_changed.assert_awaited_once()


class Prompt(unittest.TestCase):
    def test_the_moment_is_one_fixed_line_and_nothing_else_changes(self) -> None:
        plain = fight_prompt.build(base(), 2, ["Mira: hi"])
        timed = fight_prompt.build(base(), 2, ["Mira: hi"], moment="It is your turn next")
        self.assertEqual(timed, plain.replace("\n", "\nRight now: It is your turn next\n", 1))
        self.assertTrue(set(fightbot.MOMENT_LINES.values()) <= {
            "It is your turn next",
            "Your side acts now",
            "A new round of initiative is starting",
            "The DM is asking what everyone will do this round",
        })  # fmt: skip


class TheBotsEar(unittest.TestCase):
    """Which lines reach the hearing: voice lines of people who agreed and a DM's typed lines;
    never a player's typed chat, never a line still waiting for its off-topic label."""

    def press(self, *, user: int, typed: bool = False, held: bool = False) -> list[Any]:
        from unittest import mock

        from dmbot.bot import DMBot

        bot = mock.MagicMock(spec=DMBot)
        bot.clocks = None
        table = SimpleNamespace(dm_user_ids={DM}, guild_id=1)
        with mock.patch.object(fightbot, "note_line", return_value=False) as heard:
            DMBot._act_on_line(bot, table, user, "players go first", typed=typed, held=held)
        return list(heard.call_args_list)

    def test_voice_lines_reach_it(self) -> None:
        self.assertEqual(len(self.press(user=DM)), 1)
        self.assertEqual(len(self.press(user=PLAYER)), 1)
        self.assertTrue(self.press(user=DM)[0].kwargs["is_dm"])
        self.assertFalse(self.press(user=PLAYER)[0].kwargs["is_dm"])

    def test_a_dms_typed_line_reaches_it_a_players_does_not(self) -> None:
        self.assertEqual(len(self.press(user=DM, typed=True)), 1)
        self.assertEqual(self.press(user=PLAYER, typed=True), [])

    def test_a_line_still_waiting_for_its_label_does_not(self) -> None:
        self.assertEqual(self.press(user=DM, held=True), [])


if __name__ == "__main__":
    unittest.main()
