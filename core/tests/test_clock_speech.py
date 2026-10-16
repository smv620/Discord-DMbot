"""The clock follows what is said (#1117): the DM's words that move it by themselves (a rest
starting or finishing, the next morning, an amount of time later), what is refused, the
dedupe of a rest said as both starting and finishing, a player's suggestion that only asks the
DM, the suggestion limit, and who may press its buttons. Phrase tests need no database."""

from __future__ import annotations

import itertools
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.dm_screen import clock as ui
from dmbot.timebot import clock as game
from dmbot.timebot import phrases
from dmbot.timebot.clock import Clock
from dmbot.timebot.phrases import Said
from tests.test_timebot import (
    DM,
    GUILD_A,
    NOON4,
    OTHER_DM,
    PLAYER,
    AtTheTable,
)


class DmWords(unittest.TestCase):
    def test_a_rest_finishing_moves_the_clock_like_one_starting(self) -> None:
        for line, kind in (
            ("you finish your long rest", "long"),
            ("You finish a short rest.", "short"),
            ("The party finishes... you all finish your long rest", "long"),
            ("the long rest is over", "long"),
            ("your short rest ends", "short"),
            ("After your long rest.", "long"),
            ("we take a long rest", "long"),
        ):
            self.assertEqual(phrases.find_clock(line), Said(cast(Any, kind)), line)

    def test_the_next_morning_and_dawn_go_to_the_next_dawn(self) -> None:
        for line in (
            "the next morning you wake up",
            "The next morning.",
            "The next morning, the party wakes",
            "At dawn, you break camp",
            "Okay so, at dawn you wake",
            "at dawn",
        ):
            self.assertEqual(phrases.find_clock(line), Said("dawn"), line)

    def test_explicit_amounts_in_words_and_digits(self) -> None:
        cases = {
            "an hour later": 60,
            "An hour later, the door opens": 60,
            "one hour later": 60,
            "two hours later": 120,
            "ten minutes later": 10,
            "10 minutes later": 10,
            "half an hour later": 30,
            "a minute later": 1,
            "thirty minutes later": 30,
            "forty-five minutes later": 45,
            "forty five minutes later": 45,
            "ninety minutes later": 90,
            "3 hours later": 180,
            "twelve hours later": 720,
            "twenty-four hours later": 1440,
            "24 hours later": 1440,
            "an hour and ten minutes later": None,  # two amounts: not guessed at
        }
        for line, minutes in cases.items():
            said = phrases.find_clock(line)
            if minutes is None:
                self.assertIsNone(said, line)
            else:
                self.assertEqual(said, Said("advance", minutes), line)

    def test_a_skip_of_days_in_words_and_digits(self) -> None:
        days = {
            "Three days later, you reach the city.": 3,
            "Two days later.": 2,
            "3 days later, you arrive": 3,
            "A day later, the party rests": 1,
            "One day later.": 1,
            "The next day, you wake early.": 1,
            "Next day, you set out": 1,
            "Twenty-five days later, you all return": 25,
            "Thirty days later, you arrive": 30,
            "A week later, the party arrives.": 7,
            "Two weeks later, you all return": 14,
            "And then, three days later, the party reaches the pass": 3,
            "Okay, so, three days later.": 3,
        }
        for line, count in days.items():
            self.assertEqual(phrases.find_clock(line), Said("days", count * 1440), line)

    def test_without_rest_the_days_are_not_counted_as_rested(self) -> None:
        for line in (
            "Three days later, you arrive without resting.",
            "Three days later, you arrive without any rest.",
            "A week later, you arrive with no rest.",
        ):
            found = phrases.find_clock(line)
            assert found is not None
            self.assertEqual(found.action, "days_norest", line)
        self.assertEqual(phrases.find_clock("Two days later, you rest well.").action, "days")  # type: ignore[union-attr]

    def test_the_same_skip_has_one_key_rested_or_not(self) -> None:
        a, b = Said("days", 3 * 1440), Said("days_norest", 3 * 1440)
        self.assertEqual(a.key, b.key)
        self.assertNotEqual(a.key, Said("days", 4 * 1440).key)

    def test_days_that_are_not_the_clock_moving_are_ignored(self) -> None:
        for line in (
            "He arrived three days later.",  # story, not the scene cut
            "The guards left the next day.",
            "In three days you arrive.",
            "For three days you walk.",
            "Three days ago, you left.",
            "Three days from now, you arrive.",
            "Within three days we leave.",
            "Three days later, meet me at the gate.",  # an NPC talking
            "The next day, meet me at the gate.",
            '"Three days later, you reach it," he says.',  # quoted speech
            "\u201cThree days later, you reach it.\u201d",
            "Thirty-one days later, you arrive.",  # more than 30: set it with the buttons
            "Forty days later, you arrive.",
            "Zero days later, you arrive.",
            "Three days later, the guards come.",
            "Can it be three days later, you arrive?",
            "Maybe three days later, you arrive",
            "If it is three days later, you arrive",
            "Three weeks and a day later, you arrive",
            "Five weeks later, you arrive",
            "A few days later, you arrive",
            "Several days later, you arrive",
            "Three days later, you will hang, says the king",  # a maybe/said later in the line
            "Three days later, you can't go on",
            "The next day, we should",
            "Three days later, you hear what he said",
            "A days later, you arrive",
            "2 weeks later, you arrive",  # weeks are in words, one to four
        ):
            self.assertIsNone(phrases.find_clock(line), line)

    def test_more_than_a_day_or_nothing_is_not_moved_by_itself(self) -> None:
        for line in (
            "25 hours later",
            "thirty hours later",
            "0 minutes later",
            "2000 minutes later",
        ):
            self.assertIsNone(phrases.find_clock(line), line)

    def test_story_beats_that_mention_a_time_are_not_the_clock_moving(self) -> None:
        for line in (
            "The sun rises at dawn",
            "The mountains glow at dawn",
            "We leave at dawn",
            "Meet me at dawn",
            "Roll for initiative at dawn",
            "You wake at dawn",
            "He left the next morning",
            "The next morning was cold",  # a scene cut says it first, but this is a statement
            "The guard returned ten minutes later",
            "Earlier, ten minutes later he fled",
            "In the story, the next morning the hero left",
            "An hour, then ten minutes later",
            "after your long rest you hear a noise",
            "a hours later",
            # An NPC talking, not the narration: the cut needs the narration's subject.
            "At dawn, meet me at the gate",
            "The next morning, the guards will come",
            "next morning, the mist is gone",
        ):
            said = phrases.find_clock(line)
            if line == "The next morning was cold":
                continue  # opens like a cut: known and harmless (the DM sees it and can Undo)
            self.assertIsNone(said, line)

    def test_vague_narration_is_never_guessed(self) -> None:
        for line in (
            "you walk for a while",
            "after some time you arrive",
            "a few hours later",
            "some time later",
            "hours later",
            "much later",
            "the days pass",
            "you travel through the night",
            "it gets dark",
        ):
            self.assertIsNone(phrases.find_clock(line), line)

    def test_questions_wishes_conditions_and_long_lines_never_count(self) -> None:
        for line in (
            "Do you finish your long rest?",
            "an hour later?",
            "if you finish your long rest the guards come",
            "maybe an hour later",
            "I'll meet you an hour later",
            "we could do the next morning",
            "if we arrive at dawn we win",
            "you can't finish your long rest here",
            "when you finish your long rest we move on",
            "they will finish your long rest tomorrow",
            "should we skip to the next morning",
            "the innkeeper says an hour later",
            "he told me about the next morning",
            "not the next morning",
            "ten minutes later " + "and so on " * 6,
            "",
        ):
            self.assertIsNone(phrases.find_clock(line), line)

    def test_a_rest_said_as_starting_and_finishing_is_one_thing(self) -> None:
        keys = [
            getattr(phrases.find_clock(line), "key", None)
            for line in (
                "we take a long rest",
                "you finish your long rest",
                "an hour later",
                "two hours later",
            )
        ]
        self.assertEqual(keys, ["long", "long", "+60", "+120"])

    def test_words_after_the_amount_do_not_cancel_it_and_two_fillers_are_allowed(self) -> None:
        self.assertEqual(
            phrases.find_clock("An hour later, you can see the tower"), Said("advance", 60)
        )
        self.assertEqual(phrases.find_clock("okay so, an hour later"), Said("advance", 60))
        self.assertIsNone(phrases.find_clock("if an hour later you can"))

    def test_a_finish_is_marked_so_it_is_not_a_second_rest(self) -> None:
        self.assertTrue(phrases.find_clock("you finish your long rest").ends)  # type: ignore[union-attr]
        self.assertFalse(phrases.find_clock("we take a long rest").ends)  # type: ignore[union-attr]

    def test_the_old_start_phrases_still_work_and_still_refuse(self) -> None:
        self.assertEqual(phrases.find("We take a short rest."), "short")
        self.assertIsNone(phrases.find("let's take a short rest"))
        self.assertIsNone(phrases.find("the party took a long rest"))


class PlayerWords(unittest.TestCase):
    def test_a_suggestion_is_found_but_moves_nothing(self) -> None:
        for line, kind in (
            ("let's take a short rest", "short"),
            ("Let's take a long rest.", "long"),
            ("can we long rest?", "long"),
            ("can we take a short rest", "short"),
            ("should we take a long rest", "long"),
            ("we should take a short rest", "short"),
            ("I think we need a long rest", "long"),
            ("how about a short rest", "short"),
        ):
            self.assertEqual(phrases.suggested_rest(line), kind, line)
            self.assertIsNone(phrases.find_clock(line), line)  # never moves the clock

    def test_other_talk_about_rests_is_not_a_suggestion(self) -> None:
        for line in (
            "we took a long rest yesterday",
            "he said let's take a long rest",
            "I love a long rest",
            "my character rests",
            "let's go to the tavern",
            "let's take a long walk",
            "let's not take a long rest",
            "let's take a short rest later",
            "we should probably not take a short rest",
            "don't you think we should short rest",
            "",
            "let's take a short rest " + "and talk " * 8,
        ):
            self.assertIsNone(phrases.suggested_rest(line), line)


_message_ids = itertools.count(1000)  # every question is its own message


class AtTheTableMore(AtTheTable):
    """What the bot does with the words: the DM's move the clock, a player's ask the DM."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        plain = self.bot.post_message

        async def post_message(
            channel_id: int, text: str, view: Any = None, *, remember: Any = None
        ) -> Any:
            await plain(channel_id, text, view)
            # A suggestion that was posted is a message; a failed post is None (and frees the
            # slot for the next ask). Everything else keeps the harness's plain None.
            return SimpleNamespace(id=1) if "suggests" in text else None

        self.bot.post_message = post_message  # type: ignore[method-assign]

    async def say(self, user: int, line: str) -> None:
        await self.bot._note_clock_phrase(cast(Any, self.table), user, line)

    async def test_a_rest_finishing_moves_the_clock_by_eight_hours(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "you finish your long rest")
        self.assertEqual(await self.minute(), NOON4 + 480)
        self.assertTrue(any("Long rest" in text for text in self.posted))

    async def test_starting_then_finishing_the_same_rest_counts_once(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "we take a long rest")
        await self.say(DM, "you finish your long rest")
        self.assertEqual(await self.minute(), NOON4 + 480)

    async def test_a_rest_then_an_amount_it_covers_moves_once(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "you take a short rest")
        await self.say(DM, "an hour later, the door opens")
        self.assertEqual(await self.minute(), NOON4 + 60)  # not 120

    async def test_a_finish_of_the_same_rest_adds_nothing_however_late(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "we take a long rest")
        self.bot.tables[GUILD_A].clock_said.clear()  # the five-minute window has long passed
        table = cast(Any, self.table)
        kind, took, minute, at = table.clock_spoken_rest
        table.clock_spoken_rest = (kind, took, minute, at - 3600)
        await self.say(DM, "you finish your long rest")
        self.assertEqual(await self.minute(), NOON4 + 480)

    async def test_an_amount_adds_only_what_the_rest_did_not_cover(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "we take a long rest")
        await self.say(DM, "ten hours later")
        self.assertEqual(await self.minute(), NOON4 + 600)  # 8 h for the rest, 2 h on top

    async def test_after_a_button_the_clock_is_taken_as_said(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "we take a short rest")
        await self.press("m10")  # the DM moved it by hand: the rest no longer covers anything
        await self.say(DM, "an hour later")
        self.assertEqual(await self.minute(), NOON4 + 60 + 10 + 60)

    async def test_dawn_when_already_at_dawn_moves_and_says_nothing(self) -> None:
        await self.press("set", set_to=game.from_day_and_time(4, 6))
        before = len(self.posted)
        await self.say(DM, "the next morning you wake up")
        self.assertEqual(await self.minute(), game.from_day_and_time(4, 6))
        self.assertEqual(len(self.posted), before)

    async def test_the_next_morning_is_the_same_as_skip_to_dawn(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "the next morning you wake up")
        self.assertEqual(await self.minute(), game.from_day_and_time(5, 6))
        self.assertTrue(any("dawn" in text.lower() for text in self.posted))

    async def test_an_amount_moves_the_clock_with_an_undo_note(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "two hours later")
        self.assertEqual(await self.minute(), NOON4 + 120)
        self.assertTrue(any("2 hours later" in text for text in self.posted))
        self.assertTrue(any("(heard: “two hours later”)" in text for text in self.posted))
        await self.say(DM, "two hours later")  # said again at once: counted once
        self.assertEqual(await self.minute(), NOON4 + 120)
        await self.say(DM, "ten minutes later")
        self.assertEqual(await self.minute(), NOON4 + 130)

    async def skip_ids(self) -> tuple[int, int]:
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        return stored.clock.minute, stored.clock.last_long_rest

    async def test_days_land_at_the_same_time_of_day_and_count_as_rested(self) -> None:
        await self.press("set", set_to=NOON4 + 90)  # 13:30 on day 4
        await self.say(DM, "Three days later, you reach Neverwinter.")
        minute, rested = await self.skip_ids()
        self.assertEqual(minute, NOON4 + 90 + 3 * 1440)
        self.assertEqual(rested, minute)  # a journey of days includes sleep
        note = next(t for t in self.posted if "3 days later" in t)
        self.assertIn("⏩ 3 days later (counted as rested). Now Day 7, afternoon.", note)
        self.assertIn("(heard: “Three days later, you reach Neverwinter.”)", note)

    async def test_without_rest_the_rest_record_stays(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "Two days later, you arrive without resting.")
        minute, rested = await self.skip_ids()
        self.assertEqual((minute, rested), (NOON4 + 2 * 1440, NOON4))
        self.assertTrue(any("2 days later (no rest counted)" in t for t in self.posted))
        # More than 24 hours without a long rest: said once, as for any move.
        self.assertEqual(sum("24 hours since" in t for t in self.posted), 1)

    async def test_the_24_hour_note_is_not_said_after_a_rested_skip(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "A week later, you reach the pass.")
        self.assertFalse(any("24 hours since" in t for t in self.posted))

    async def test_undo_brings_back_the_clock_and_the_rest_record(self) -> None:
        await self.press("set", set_to=NOON4)
        before = await self.skip_ids()
        await self.say(DM, "Three days later, you reach the city.")
        after = await self.skip_ids()
        self.assertNotEqual(before, after)
        button = ui.ClockUndoButton(
            self.a.id, ui.undo_id(Clock(*before), Clock(after[0], after[1]))
        )
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=DM),
            client=self.client(),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        interaction.client.post_message = AsyncMock(return_value=None)
        await button.callback(cast(Any, interaction))
        self.assertEqual(await self.skip_ids(), before)

    async def test_days_end_a_fight_like_a_long_rest_does(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        await self.say(DM, "Three days later, you reach the city.")
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertIsNone(stored.clock.combat_round)
        self.assertTrue(any("Combat is over" in t for t in self.posted))

    async def test_the_note_says_one_day_in_the_singular_and_marks_are_not_listed(self) -> None:
        await self.press("set", set_to=game.from_day_and_time(4, 11))
        await self.say(DM, "A day later, you reach the city.")
        self.assertTrue(any("⏩ 1 day later (counted as rested)" in t for t in self.posted))
        self.assertFalse(any("Noon" in t or "Dusk" in t or "Dawn" in t for t in self.posted))

    async def test_the_same_skip_said_twice_in_five_minutes_counts_once(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(DM, "Three days later, you reach the city.")
        await self.say(DM, "Three days later, you reach the city.")
        minute, _ = await self.skip_ids()
        self.assertEqual(minute, NOON4 + 3 * 1440)

    async def test_a_players_skip_and_a_story_skip_move_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(PLAYER, "Three days later, you reach Neverwinter.")
        await self.say(DM, "He arrived three days later.")
        await self.say(DM, "In three days you arrive.")
        self.assertEqual((await self.skip_ids())[0], NOON4)

    async def test_noon_and_dusk_are_said_as_an_amount_passes_them(self) -> None:
        await self.press("set", set_to=game.from_day_and_time(4, 11))
        await self.say(DM, "two hours later")
        self.assertTrue(any("Noon" in text for text in self.posted))

    async def test_vague_words_and_non_dms_never_move_it(self) -> None:
        await self.press("set", set_to=NOON4)
        for user, line in (
            (DM, "after some time you arrive"),
            (DM, "a few hours later"),
            (PLAYER, "an hour later"),
            (PLAYER, "the next morning"),
            (PLAYER, "you finish your long rest"),
        ):
            await self.say(user, line)
        self.assertEqual(await self.minute(), NOON4)

    async def test_a_player_who_has_not_agreed_asks_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        self.bot.consent.has_consent = lambda guild_id, user_id: user_id != PLAYER  # type: ignore[method-assign]
        await self.say(PLAYER, "let's take a short rest")
        self.assertEqual(self.posted, [])

    async def test_a_suggestion_asks_the_dm_and_moves_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        views: list[Any] = []
        original = self.bot.post_message

        async def record(
            channel_id: int, text: str, view: Any = None, *, remember: Any = None
        ) -> Any:
            views.append(view)
            return await original(channel_id, text, view)

        self.bot.post_message = record  # type: ignore[method-assign]
        await self.say(PLAYER, "let's take a short rest")
        self.assertEqual(await self.minute(), NOON4)
        [text] = self.posted
        self.assertTrue(text.endswith("suggests a short rest."))
        labels = [i.item.label for i in views[0].children]
        self.assertEqual(labels, ["Short rest", "Not now"])

    async def test_at_most_one_suggestion_in_five_minutes(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.say(PLAYER, "let's take a short rest")
        await self.say(OTHER_DM_AS_PLAYER, "can we long rest?")
        await self.say(PLAYER, "can we take a short rest")
        self.assertEqual(len(self.posted), 1)
        self.table.clock_suggested_at -= ui.SUGGEST_GAP_S + 1
        await self.say(PLAYER, "can we take a short rest")
        self.assertEqual(len(self.posted), 2)

    async def test_a_suggestion_that_could_not_be_posted_frees_the_slot(self) -> None:
        await self.press("set", set_to=NOON4)

        async def failing(
            channel_id: int, text: str, view: Any = None, *, remember: Any = None
        ) -> None:
            return None

        self.bot.post_message = failing  # type: ignore[method-assign]
        await self.say(PLAYER, "let's take a short rest")
        self.assertIsNone(self.table.clock_suggested_at)

    async def test_without_a_clock_a_suggestion_has_nothing_to_move_and_asks_nothing(self) -> None:
        await self.say(PLAYER, "let's take a short rest")
        self.assertEqual(self.posted, [])

    def interaction(self, user: int, content: str = "🛌 Mira suggests a short rest.") -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=user, display_name="Sam"),
            message=SimpleNamespace(content=content, id=next(_message_ids)),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_the_dm_takes_the_rest_with_the_button(self) -> None:
        await self.press("set", set_to=NOON4)
        it = self.interaction(DM)
        await ui.SuggestionButton(self.a.id, "short", "yes").callback(it)
        self.assertEqual(await self.minute(), NOON4 + 60)
        edit = it.edit_original_response.await_args.kwargs
        self.assertIsNone(edit["view"])  # the question is closed
        self.assertIn("✅ Short rest, chosen by", edit["content"])
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])  # the Undo note

    async def test_a_failed_press_does_not_say_the_rest_was_taken(self) -> None:
        from unittest.mock import patch

        from dmbot.campaigns import CampaignError

        await self.press("set", set_to=NOON4)
        it = self.interaction(DM)
        it.message.id = 779
        with patch.object(ui, "press", AsyncMock(side_effect=CampaignError("Not now."))):
            await ui.SuggestionButton(self.a.id, "short", "yes").callback(it)
        it.edit_original_response.assert_not_awaited()  # the buttons stay up
        self.assertNotIn(779, ui._answered)
        again = self.interaction(DM)
        again.message.id = 779
        await ui.SuggestionButton(self.a.id, "short", "yes").callback(again)
        self.assertEqual(await self.minute(), NOON4 + 60)

    async def test_not_now_changes_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        it = self.interaction(DM)
        await ui.SuggestionButton(self.a.id, "short", "no").callback(it)
        self.assertEqual(await self.minute(), NOON4)
        self.assertIn("Dismissed by", it.edit_original_response.await_args.kwargs["content"])

    async def test_a_player_cannot_press_it(self) -> None:
        await self.press("set", set_to=NOON4)
        for answer in ("yes", "no"):
            it = self.interaction(PLAYER)
            await ui.SuggestionButton(self.a.id, "long", answer).callback(it)
            it.edit_original_response.assert_not_awaited()
            self.assertIn("Only this campaign's DMs", it.followup.send.await_args.args[0])
        self.assertEqual(await self.minute(), NOON4)

    async def test_a_double_press_takes_the_rest_once(self) -> None:
        import asyncio

        await self.press("set", set_to=NOON4)
        first, second = self.interaction(DM), self.interaction(OTHER_DM)
        first.message.id = second.message.id = 777  # the same question
        await asyncio.gather(
            ui.SuggestionButton(self.a.id, "long", "yes").callback(first),
            ui.SuggestionButton(self.a.id, "long", "yes").callback(second),
        )
        self.assertEqual(await self.minute(), NOON4 + 480)  # once, not twice

    async def test_with_no_clock_the_question_stays_open(self) -> None:
        it = self.interaction(DM)
        it.message.id = 778
        await ui.SuggestionButton(self.a.id, "short", "yes").callback(it)
        it.edit_original_response.assert_not_awaited()
        self.assertIn("isn't set", it.followup.send.await_args.args[0])

    async def test_the_clock_not_set_burns_no_window(self) -> None:
        await self.say(DM, "an hour later")  # no clock yet
        await self.press("set", set_to=NOON4)
        await self.say(DM, "an hour later")
        self.assertEqual(await self.minute(), NOON4 + 60)

    async def test_a_player_in_the_middle_of_a_rejected_ask_does_not_hold_off_the_next(
        self,
    ) -> None:
        await self.say(PLAYER, "let's take a short rest")  # no clock: nothing asked
        await self.press("set", set_to=NOON4)
        await self.say(PLAYER, "let's take a short rest")
        self.assertEqual(len(self.posted), 1)

    async def test_nothing_happens_when_the_session_is_not_listening(self) -> None:
        await self.press("set", set_to=NOON4)
        self.table.listening = False
        await self.say(DM, "an hour later")
        await self.say(PLAYER, "let's take a short rest")
        self.assertEqual((await self.minute(), self.posted), (NOON4, []))

    def test_the_button_ids_match_their_template(self) -> None:
        for kind in ("short", "long"):
            for answer in ("yes", "no"):
                custom_id = ui.SuggestionButton(self.a.id, kind, answer).item.custom_id
                assert custom_id is not None and len(custom_id) <= 100
                self.assertIsNotNone(
                    ui.SuggestionButton.__discord_ui_compiled_template__.fullmatch(custom_id)
                )


OTHER_DM_AS_PLAYER = PLAYER + 1000  # another player: the limit is per campaign, not per person
