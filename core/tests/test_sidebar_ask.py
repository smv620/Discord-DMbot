"""Asking the DM sidebar out loud (#935): which lines start a question, and which never do."""

from __future__ import annotations

import unittest

from dmbot.sidebar.ask import ASK_PER_MINUTE, ASK_PER_SESSION, AskLimiter, request_in


class RequestInTests(unittest.TestCase):
    def test_the_example_from_the_plan(self) -> None:
        found = request_in("Hold on, I need to find if you need line of sight for fireball")
        assert found is not None
        self.assertEqual(found.verb, "find")
        self.assertEqual(found.rest, "if you need line of sight for fireball")
        self.assertEqual(found.question, "find if you need line of sight for fireball")

    def test_other_ways_to_say_it(self) -> None:
        for text, question in [
            ("Hang on, I have to look up the grapple rules.", "look up the grapple rules"),
            ("One sec, let me check what a shield spell does", "check what a shield spell does"),
            (
                "one sec I gotta find out how long a short rest is",
                "find out how long a short rest is",
            ),
            (
                "Okay, hold on. I need to check the rules for flanking",
                "check the rules for flanking",
            ),
            (
                "Sorry, give me a second, I need to look up the exhaustion rules.",
                "look up the exhaustion rules",
            ),
        ]:
            with self.subTest(text=text):
                found = request_in(text)
                assert found is not None
                self.assertEqual(found.question, question)

    def test_a_one_word_topic_works(self) -> None:
        found = request_in("Hold on, let me look up Fireball")
        assert found is not None
        self.assertEqual(found.question, "look up Fireball")
        found = request_in("Hang on, I need to check grappling.")
        assert found is not None
        self.assertEqual(found.rest, "grappling")

    def test_rules_are_not_housekeeping(self) -> None:
        for text in [
            "Hold on, I need to check the rules for flanking",
            "Hold on, let me look up the grapple rules",
            "Hold on, let me check the spell description",
        ]:
            with self.subTest(text=text):
                self.assertIsNotNone(request_in(text))

    def test_only_up_to_the_end_of_the_sentence(self) -> None:
        found = request_in("Hold on, I need to check how grappling works. Anyway, you wait.")
        assert found is not None
        self.assertEqual(found.rest, "how grappling works")

    def test_lines_that_must_not_start_one(self) -> None:
        for text in [
            "I need to find the map",  # no lead-in: just talk
            "The old man says: I need to find the map before dawn",
            "Hold on, I need to find my dice",  # the DM's own things
            "Hold on, I need to check my notes",
            "Hold on, I need to find it",  # too little to look up
            "Hold on, let me check the map",  # the DM's housekeeping
            "Hold on, I need to find the page",
            "Hold on, let me check the module",
            "Hold on, let me look at the book",
            "Hold on, I have to check the notes",
            "Hold on, let me check that",
            "Hold on, let me find something",
            "Hold on, he needs to find the key",  # someone else
            "Hold on",
            "I need to look up",
            "",
            "Let me check, uh, never mind",
            "Sorry, I'll check the door for traps",  # narration, no hold-on
            "Wait I'll check what the goblin does",
            "Okay let me check the map and see",
            "Wait, I need to find the key in the chest",  # a bare 'wait' is not a lead-in
        ]:
            with self.subTest(text=text):
                self.assertIsNone(request_in(text))

    def test_a_line_the_cleaner_tagged_as_in_character_never_does(self) -> None:
        text = "Hold on, I need to find the dragon's lair on this map"
        self.assertIsNotNone(request_in(text))
        self.assertIsNone(request_in(text, in_character=True))

    def test_a_very_long_question_is_cut_at_a_word(self) -> None:
        found = request_in("Hold on, I need to find " + "word " * 200)
        assert found is not None
        self.assertLessEqual(len(found.rest), 240)
        self.assertTrue(found.rest.endswith("word"))


class LongLines(unittest.TestCase):
    def test_garbled_or_endless_lines_are_quick(self) -> None:
        import time

        for text in [
            "hold on, " * 3000,
            "so um uh " * 2000,
            "hold on " * 3000 + "i need to",
            "ok. " * 3000 + "i need to find",
        ]:
            started = time.perf_counter()
            request_in(text)
            self.assertLess(time.perf_counter() - started, 0.05, text[:20])


class WakePhrase(unittest.TestCase):
    """#1040: "Hey DMbot, ..." anywhere in the DM's line (people run it into what came before),
    however speech-to-text writes the name; the question is what follows it."""

    QUESTION = "what's the range of fireball"
    NAMES = ("DMbot", "dmbot", "DM bot", "D.M. bot", "D M bot", "DM-bot", "Dee em bot", "the M bot")

    def question(self, said: str) -> str | None:
        got = request_in(said)
        return got.question if got else None

    def test_every_spelling_at_the_start_of_a_line(self) -> None:
        for name in self.NAMES:
            for lead in ("", "Hey ", "hey, ", "Okay "):
                with self.subTest(f"{lead}{name}"):
                    self.assertEqual(
                        self.question(f"{lead}{name}, {self.QUESTION}?"), self.QUESTION
                    )

    def test_in_the_middle_and_near_the_end_of_a_long_line(self) -> None:
        story = "so they walk down the hall and the torches flicker and they are in the cave"
        for name in self.NAMES:
            with self.subTest(name):
                self.assertEqual(
                    self.question(f"{story}, hey {name} {self.QUESTION}"), self.QUESTION
                )
        long_line = "and then the party goes on and on " * 12 + f"hey DMbot {self.QUESTION}"
        self.assertLess(len(long_line), 600)
        self.assertEqual(self.question(long_line), self.QUESTION)

    def test_hey_wakes_it_without_a_pause_but_a_bare_name_needs_one_or_a_question_word(
        self,
    ) -> None:
        self.assertEqual(
            self.question("so hey DM bot what is the casting time of shield"),
            "what is the casting time of shield",
        )
        self.assertEqual(self.question("and then DMbot, what's the range"), "what's the range")
        self.assertEqual(self.question("DMbot is fireball a good idea"), "is fireball a good idea")
        self.assertEqual(
            self.question("DMbot check how grappling works"), "check how grappling works"
        )
        self.assertEqual(self.question("I told DMbot what's the range of fireball"), self.QUESTION)

    def test_a_story_or_a_mention_does_nothing(self) -> None:
        for said in (
            "and DMbot said earlier the range was long",
            "the DMbot screen is on",
            "DMbot said the goblin is fast",
            "DM bot was wrong about the range",
            "I like the dmbot app",
            "DMbot later told us nothing",
        ):
            with self.subTest(said):
                self.assertIsNone(request_in(said))

    def test_two_in_one_line_the_last_one_asks(self) -> None:
        said = "hey DMbot what about fireball, no wait, hey DMbot what's the range of shield"
        self.assertEqual(self.question(said), "what's the range of shield")

    def test_a_bad_last_mention_falls_back_to_the_real_call_before_it(self) -> None:
        self.assertEqual(
            self.question("hey DMbot what about prone and DMbot said nothing"),
            "what about prone and DMbot said nothing",
        )

    def test_nothing_after_the_name_is_not_a_question(self) -> None:
        for said in ("Hey DMbot", "Hey DMbot.", "Hey DMbot, it", "DMbot,"):
            with self.subTest(said):
                self.assertIsNone(request_in(said))

    def test_an_in_character_line_never_wakes_it(self) -> None:
        self.assertIsNone(request_in(f"Hey DMbot, {self.QUESTION}", in_character=True))

    def test_hold_on_still_works(self) -> None:
        got = request_in("Hold on, I need to find the range of fireball.")
        assert got is not None
        self.assertEqual(got.question, "find the range of fireball")

    def test_a_long_garbled_line_is_quick(self) -> None:
        import time

        started = time.perf_counter()
        for text in (
            "hey " * 3000 + "dmbot",
            "dmbot " + ", " * 3000,
            "dee em bot " * 600,
            "the m bot hey " * 500,
        ):
            request_in(text)
        self.assertLess(time.perf_counter() - started, 0.1)


class AskLimiterTests(unittest.TestCase):
    def test_six_a_minute(self) -> None:
        limit = AskLimiter()
        for i in range(ASK_PER_MINUTE):
            self.assertTrue(limit.allow(100.0 + i))
        self.assertFalse(limit.allow(106.0))
        self.assertFalse(limit.allow(159.9))  # a refused one doesn't restart the minute
        self.assertTrue(limit.allow(160.0))  # the first of the six was a minute ago

    def test_a_hundred_and_twenty_a_session(self) -> None:
        limit = AskLimiter()
        now = 0.0
        for _ in range(ASK_PER_SESSION):
            self.assertTrue(limit.allow(now))
            now += 61.0  # never more than one a minute
        self.assertFalse(limit.allow(now))

    def test_the_old_one_a_minute_limit_is_gone(self) -> None:
        limit = AskLimiter()
        self.assertTrue(limit.allow(100.0))
        self.assertTrue(limit.allow(101.0))


if __name__ == "__main__":
    unittest.main()
