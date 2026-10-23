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
from dmbot.memory.sheet_store import CharacterSheet, FightPlayer
from dmbot.timebot.clock import Clock
from tests import speed
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


class Ground(tf.Harness):
    """FightBot's tick for each heard style. The harness is test_fightbot's Runner."""

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


class Timed(Ground):
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
        self.assertIn("Right now: The DM just called your name", self.last_prompt(bot))
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
        self.assertIn("The DM seems to be calling your turn next", self.last_prompt(bot))

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
        players = {11: FightPlayer(last_call_at=int(self.now) - 10)}  # called 10 s ago
        bot.sheets.fight_players = AsyncMock(side_effect=lambda g, c: dict(players))
        self.dm_says(table, "Hero11, you're on deck")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 0)  # the 2-minute cap holds it back
        self.assertIn(11, table.fight.due)  # and it waits
        self.dm_says(table, "goblin one, its turn")  # the turn moved on
        self.now += 130
        players[11] = FightPlayer(last_call_at=int(self.now) - 200)
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
        self.assertIn("The DM seems to be calling your side now", self.last_prompt(bot))
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
        self.assertIn(
            "The DM seems to be starting a new round of initiative", self.last_prompt(bot)
        )

    async def test_a_call_for_declarations_reminds_everyone_and_the_cap_still_holds(self) -> None:
        table, bot = self.primed([11, 12])
        self.dm_says(table, "what's everyone doing this round?")
        self.assertEqual(table.fight.style.declarations, "yes")
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 2)
        self.assertIn("The DM seems to be asking everyone what they will do", self.last_prompt(bot))
        self.now += 20  # asked again 20 seconds later: inside the two-minute gap
        players = {u: FightPlayer(last_call_at=int(self.now) - 20) for u in (11, 12)}
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
        table.fight.secrets = ["zargothrax"]  # (kept lower-case)
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


class Hardening(Ground):
    """Found in review: lag between the round moving and FightBot knowing, late reminders,
    repeats, names that end in s, and lines that are not orders."""

    async def test_a_line_right_after_the_round_moves_is_counted_in_the_new_round(self) -> None:
        table, _bot = self.primed([11], round_number=1)
        client = SimpleNamespace(tables={table.guild_id: table})
        campaign: Any = SimpleNamespace(guild_id=table.guild_id, id=table.campaign_id)
        clock_ui._note_round(client, campaign, Clock(0, 0, None, 2, 0))  # "next round" was said
        self.assertEqual(table.fight.round, 2)
        self.dm_says(table, "declare your actions")  # said before the next 30-second pass
        self.assertEqual({d.round for d in table.fight.due.values()}, {2})
        self.assertEqual(set(table.fight.due), {11})
        clock_ui._note_round(client, campaign, Clock(0, 0))  # the fight ended
        self.assertEqual((table.fight.round, table.fight.due), (None, {}))

    async def test_a_moment_that_passed_while_the_answer_was_written_is_not_sent(self) -> None:
        table, bot = self.primed([11])
        self.dm_says(table, "Hero11, you're on deck")

        async def slow(*_a: Any, **_k: Any) -> Any:
            self.dm_says(table, "goblin one, its turn")  # the turn moved on during the call
            return await original(*_a, **_k)

        original = bot.fight_ai.complete
        bot.fight_ai.complete = slow
        await self.tick(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_a_moment_expires_after_a_few_minutes(self) -> None:
        table, bot = self.primed([11])
        self.dm_says(table, "players go first")
        self.now += fightbot.MOMENT_TTL_S + 1
        players = {11: FightPlayer()}
        bot.sheets.fight_players = AsyncMock(side_effect=lambda g, c: dict(players))
        await self.tick(bot, table)
        self.assertEqual(self.calls(bot), 0)

    async def test_the_last_creature_in_the_order_is_followed_by_the_first(self) -> None:
        table, _bot = self.primed([11], round_number=2)
        table.fight.style.vote("order", "each", DM, from_dm=True, consenting=YES)
        table.fight.order_seen = [("p", 11), ("m", "goblin 1")]
        self.dm_says(table, "goblin one, its turn")
        self.assertEqual(table.fight.due[11].kind, "next")

    async def test_the_same_turn_called_twice_is_one_turn(self) -> None:
        table, _bot = self.primed([11])
        self.dm_says(table, "goblin one, its turn")
        self.dm_says(table, "goblin one, its turn")
        self.assertEqual(table.fight.turn_no, 1)

    async def test_players_remarks_outside_a_fight_set_nothing(self) -> None:
        table, _bot = self.primed([11, 12])
        table.fight.round = None
        self.player_says(table, 11, "side initiative")
        self.player_says(table, 12, "side initiative")
        self.assertEqual(table.fight.style.order, "unknown")

    async def test_a_name_nobody_has_is_a_style_only_from_a_dm(self) -> None:
        table, _bot = self.primed([11, 12])
        self.player_says(table, 11, "Bob, you're up")
        self.player_says(table, 12, "Bob, you're up")
        self.assertEqual(table.fight.style.order, "unknown")
        self.dm_says(table, "Bob, you're up")
        self.assertEqual(table.fight.style.order, "each")

    async def test_the_dm_is_never_a_player_to_remind_or_a_name_to_call(self) -> None:
        table, bot = self.primed([11])
        members = [SimpleNamespace(id=DM, bot=False, display_name="Dave")]
        bot.get_guild = lambda gid: SimpleNamespace(
            get_channel=lambda cid: SimpleNamespace(members=members)
        )
        self.assertNotIn("dave", fightbot._names(bot, table, []))
        self.dm_says(table, "players go first")
        self.assertNotIn(DM, table.fight.due)

    async def test_the_dm_line_only_refreshes_when_its_words_change(self) -> None:
        table, _bot = self.primed([11])
        self.assertTrue(self.dm_says(table, "players go first"))
        self.assertFalse(self.dm_says(table, "the party acts"))  # still "by side"
        self.assertFalse(fightbot.observe(table.fight, 1, {11}, YES))  # nothing new to show
        table.fight.style.infer_no_declarations()  # "no" is never on the DM's line
        self.assertEqual(table.fight.style.dm_phrase(), "Initiative, as heard: by side")


class StatedBeatsGuessed(Ground):
    async def test_a_turn_call_never_overrules_a_style_the_dm_stated(self) -> None:
        table, _bot = self.primed([11])
        self.dm_says(table, "side initiative")
        for line in ("Hero11, you're up", "goblin one, its turn", "Hero11, you're on deck"):
            self.dm_says(table, line)
        self.assertEqual(
            (table.fight.style.order, table.fight.style.set_by["order"]), ("side", "dm")
        )

    async def test_a_stated_style_replaces_one_guessed_from_turns(self) -> None:
        table, _bot = self.primed([11])
        self.dm_says(table, "goblin one, its turn")
        self.assertEqual(
            (table.fight.style.order, table.fight.style.set_by["order"]), ("each", "dm_guess")
        )
        self.dm_says(table, "players go first")
        self.assertEqual(table.fight.style.order, "side")

    async def test_players_cannot_overrule_a_dms_guess_either(self) -> None:
        table, _bot = self.primed([11, 12])
        self.dm_says(table, "goblin one, its turn")
        for user in (11, 12):
            self.player_says(table, user, "side initiative")
        self.assertEqual(table.fight.style.order, "each")

    async def test_once_declarations_are_called_no_turn_moments_this_round(self) -> None:
        table, _bot = self.primed([11, 12], round_number=2)
        table.fight.style.vote("order", "each", DM, from_dm=True, consenting=YES)
        table.fight.order_seen = [("m", "goblin 1"), ("p", 11)]
        self.dm_says(table, "Hero12, you're on deck")
        self.assertEqual(table.fight.due[12].kind, "next")
        self.dm_says(table, "declare your actions")
        self.assertEqual({d.kind for d in table.fight.due.values()}, {"declare"})  # turn/next gone
        self.dm_says(table, "Hero12, you're on deck")
        self.dm_says(table, "goblin one, its turn")  # would remind Hero11 otherwise
        self.dm_says(table, "Hero12, you're up")
        self.assertEqual({d.kind for d in table.fight.due.values()}, {"declare"})
        # the next round plays out the usual way again
        clock_ui._note_round(
            SimpleNamespace(tables={table.guild_id: table}),
            SimpleNamespace(guild_id=table.guild_id, id=table.campaign_id),
            Clock(0, 0, None, 3, 0),
        )
        self.dm_says(table, "Hero12, you're on deck")
        self.assertEqual(table.fight.due[12].kind, "next")

    async def test_a_reroll_moment_survives_the_round_changing(self) -> None:
        table, _bot = self.primed([11], round_number=2)
        self.dm_says(table, "roll initiative")  # said just as round 2 begins...
        clock_ui._note_round(
            SimpleNamespace(tables={table.guild_id: table}),
            SimpleNamespace(guild_id=table.guild_id, id=table.campaign_id),
            Clock(0, 0, None, 3, 0),
        )  # ...and the clock moves on to round 3 before the pass
        self.assertEqual(set(table.fight.due_now()), {11})
        side = self.dm_says(table, "players go first")
        self.assertTrue(side)
        table.fight.round = 5  # but not for long: two rounds on it has passed
        self.assertEqual(table.fight.due_now(), {})


class MoreNearMisses(unittest.TestCase):
    def heard(self, line: str) -> Any:
        return hear(line, {**NAMES, "chris": 9}, round_number=2)

    def test_trailing_words_make_it_a_remark_not_an_order(self) -> None:
        for line in (
            "side initiative is stupid",
            "group initiative sucks",
            "We use side initiative for the boss",
        ):
            self.assertIsNone(self.heard(line), line)
        self.assertEqual(self.heard("we use side initiative").order, "side")
        self.assertEqual(self.heard("initiative by side").order, "side")

    def test_a_name_ending_in_s_is_a_person_not_a_side(self) -> None:
        for line in ("Chris' turn", "Chris's turn"):
            got = self.heard(line)
            self.assertEqual((got.order, got.turn), ("each", (9,)), line)
        self.assertEqual(self.heard("the goblins' turn").order, "side")

    def test_narration_is_not_a_turn_call(self) -> None:
        for line in ("the bridge, it goes", "sure, you go", "the dragon's turn", "sure, you're up"):
            self.assertIsNone(self.heard(line), line)

    def test_a_possessive_turn_is_a_person_at_the_table_only(self) -> None:
        self.assertEqual(self.heard("Mira's turn").turn, (1,))
        self.assertIsNone(self.heard("the dragon's turn"))
        self.assertIsNone(self.heard("Smaug's turn"))

    def test_filler_words_are_not_names(self) -> None:
        self.assertIsNone(self.heard("okay, you're next"))
        self.assertIsNone(self.heard("it's your turn"))

    def test_only_a_real_number_is_a_roll(self) -> None:
        self.assertTrue(self.heard("I got a 17").rolled)
        self.assertTrue(self.heard("twenty one for initiative").rolled)
        self.assertIsNone(self.heard("I got it"))
        self.assertIsNone(self.heard("I have dinner"))

    def test_a_huge_line_is_ignored_quickly(self) -> None:
        import time

        start = time.perf_counter()
        self.assertIsNone(self.heard("x" * 100_000 + " you're up"))
        self.assertIsNone(self.heard("a" * 100_000))
        self.assertLess(time.perf_counter() - start, speed.allowed(0.5))


class Prompt(unittest.TestCase):
    def test_the_moment_is_one_fixed_line_and_nothing_else_changes(self) -> None:
        line = "The DM seems to be calling your turn next"
        plain = fight_prompt.build(base(), 2, ["Mira: hi"])
        timed = fight_prompt.build(base(), 2, ["Mira: hi"], moment=line)
        self.assertEqual(timed, plain.replace("\n", f"\nRight now: {line}\n", 1))

    def test_the_moment_lines_are_hedged_and_name_the_dm(self) -> None:
        self.assertEqual(set(fightbot.MOMENT_LINES), {"turn", "next", "side", "reroll", "declare"})
        for text in fightbot.MOMENT_LINES.values():
            self.assertTrue(text.startswith("The DM "), text)

    def test_the_prompt_says_the_line_may_be_wrong_and_never_to_say_whose_turn(self) -> None:
        self.assertIn("context only and may be wrong", fight_prompt.PROMPT)
        self.assertIn("never say whose turn it is", fight_prompt.PROMPT)


class TheBotsEar(unittest.TestCase):
    """Which lines reach the hearing: voice lines of people who agreed and a DM's typed lines;
    never a player's typed chat, never a line still waiting for its off-topic label."""

    def press(self, *, user: int, typed: bool = False, held: bool = False) -> list[Any]:
        from unittest import mock

        from dmbot.bot import DMBot

        bot = mock.MagicMock(spec=DMBot)
        bot.clocks = None
        table: Any = SimpleNamespace(dm_user_ids={DM}, guild_id=1)
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
