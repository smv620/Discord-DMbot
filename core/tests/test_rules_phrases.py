"""A rules phrase is never offered as a name (#5214): "Animal Handling check", "Dex save", "Insight
roll" and "Wisdom saving throw" are refused, traced `rules_word`, by the scan, the name reader and
the recap's names; "Strahd check" is a hearing of Strahd, never a new name. The list only ever
refuses: it never makes anything a name. Invented names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast

from dmbot.memory import name_reader as nr
from dmbot.memory.scan import RULES_WORD, find_new_names, is_rules_phrase, without_roll

ROLLS = ("Animal Handling check", "Dex save", "Insight roll", "Wisdom saving throw")


class TheList(unittest.TestCase):
    def test_a_rules_word_with_a_roll_after_it_is_a_rules_phrase(self) -> None:
        for name in (*ROLLS, "Sleight of Hand check", "Animal Handling", "Charisma Save"):
            with self.subTest(name=name):
                self.assertTrue(is_rules_phrase(name))

    def test_a_name_with_a_roll_after_it_is_that_name_and_never_a_rules_phrase(self) -> None:
        self.assertEqual(without_roll("Strahd check"), "Strahd")
        self.assertEqual(without_roll("Strahd saving throw"), "Strahd")
        self.assertFalse(is_rules_phrase("Strahd check"))
        # (a short ability form alone may be someone's name: only a roll after it refuses it)
        self.assertFalse(is_rules_phrase("Dex"))
        self.assertTrue(is_rules_phrase("Check"))  # (a roll word alone is a rules word too)
        self.assertEqual(without_roll("check"), "check")

    def test_the_list_holds_the_eighteen_skills_and_six_abilities(self) -> None:
        from dmbot.memory.scan import ABILITIES, ABILITY_SHORT, SKILLS

        self.assertEqual((len(SKILLS), len(ABILITIES), len(ABILITY_SHORT)), (18, 6, 6))


class TheScan(unittest.TestCase):
    LINES: tuple[str, ...] = (
        "Make an Animal Handling Check now.", "Roll an Animal Handling Check, Vell.",
        "Make a Dex Save for me.", "That Dex Save fails.", "An Insight Roll, then.",
        "Your Insight Roll is low.", "A Wisdom Saving Throw now.",
        "That Wisdom Saving Throw holds.",
    )  # fmt: skip

    def test_a_rules_phrase_is_never_offered_and_is_traced_rules_word(self) -> None:
        refused: list[tuple[str, str]] = []
        found = find_new_names(self.LINES, refusals=refused, unlimited=True)
        self.assertEqual([s.name for s in found], [])
        traced = {name for name, why in refused if why == RULES_WORD}
        # ("Insight" and "Wisdom" were always skipped as rules words: their roll is what is left)
        self.assertEqual(traced, {"Animal Handling Check", "Dex Save", "Roll", "Saving Throw"})

    def test_a_known_names_check_is_a_hearing_never_a_new_name(self) -> None:
        lines = ["Make a Strahd Check, then.", "The Strahd Check fails.", "We met Vorna there."]
        self.assertEqual(
            [s.name for s in find_new_names(lines, ["strahd"], min_times=1)], ["Vorna"]
        )
        # (not known yet: its hearings count for the name itself, never for "Strahd Check")
        lines = ["We met Strahd at noon.", "Then a Strahd Check.", "Then Strahd left."]
        (found,) = find_new_names(lines, min_times=1)
        self.assertEqual((found.name, found.times), ("Strahd", 3))


LINES = {
    "1": "make an animal handling check now",
    "2": "roll a dex save, then an insight roll",
    "3": "a wisdom saving throw for strahd's men",
    "4": "the strahd check fails",
}


def item(name: str, lines: list[int]) -> dict[str, Any]:
    return {"name": name, "kind": "person", "why": "spoken_of", "lines": lines}


class TheReader(unittest.TestCase):
    def test_a_rules_phrase_from_the_reader_is_refused_as_a_rules_word(self) -> None:
        for name, at in (("animal handling check", [1]), ("dex save", [2]), ("insight roll", [2]),
                         ("wisdom saving throw", [3])):  # fmt: skip
            with self.subTest(name=name):
                heard, why = nr.check(item(name, at), LINES, {})
                self.assertIsNone(heard)
                self.assertEqual(why, nr.RULES_WORD)

    def test_a_known_names_check_from_the_reader_is_that_name(self) -> None:
        heard, why = nr.check(item("strahd check", [4]), LINES, {"strahd": nr.KNOWN_NAME})
        self.assertIsNone(heard)
        self.assertEqual(why, nr.KNOWN_NAME)


class TheRecapsNames(unittest.IsolatedAsyncioTestCase):
    async def test_a_rules_phrase_or_a_known_names_check_is_never_queued(self) -> None:
        from dmbot.bot import DMBot
        from tests.test_recap_names import CID, DM_ID, GUILD, bot_with

        bot = bot_with()  # (it knows "mira")
        job = SimpleNamespace(
            session_at=100,
            dm_user_id=DM_ID,
            to_confirm=(*ROLLS, "Mira check", "Yrsa"),
        )
        camp = SimpleNamespace(dm_screen_channel_id=77, dm_bot_channel_id=None)
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, job, camp)
        added = [c.kwargs["name"] for c in bot.memory.add_entity.await_args_list]
        self.assertEqual(added, ["Yrsa"])  # (the list only refuses: Yrsa is still offered)
