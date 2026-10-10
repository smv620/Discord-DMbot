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
    """#1040: "Hey DMbot, ..." or "DMbot, ..." at the start of the line, however speech-to-text
    writes the name."""

    QUESTION = "what's the range of fireball"

    def test_every_spelling_at_the_start_of_a_line(self) -> None:
        for name in (
            "DMbot",
            "dmbot",
            "DM bot",
            "D.M. bot",
            "D M bot",
            "DM-bot",
            "Dee em bot",
            "the M bot",
        ):
            for lead in ("", "Hey ", "hey, ", "Okay "):
                with self.subTest(f"{lead}{name}"):
                    got = request_in(f"{lead}{name}, {self.QUESTION}?")
                    self.assertIsNotNone(got)
                    assert got is not None
                    self.assertEqual(got.question, self.QUESTION)

    def test_no_comma_is_fine_for_a_question(self) -> None:
        got = request_in("Hey DM bot what is the casting time of shield")
        assert got is not None
        self.assertEqual(got.question, "what is the casting time of shield")

    def test_the_name_in_the_middle_of_a_line_does_nothing(self) -> None:
        for said in (
            "and then DMbot, what's the range of fireball",
            "I told DMbot what's the range of fireball",
            "so the goblin says hey DMbot what's the range",
        ):
            with self.subTest(said):
                self.assertIsNone(request_in(said))

    def test_a_story_about_dmbot_does_nothing(self) -> None:
        self.assertIsNone(request_in("DMbot said the goblin is fast"))
        self.assertIsNone(request_in("DM bot was wrong about the range"))

    def test_nothing_after_the_name_is_not_a_question(self) -> None:
        for said in ("Hey DMbot", "Hey DMbot.", "DMbot, it", "DMbot,"):
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
        request_in("hey " * 3000 + "dmbot")
        request_in("dmbot " + ", " * 3000)
        self.assertLess(time.perf_counter() - started, 0.05)


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
