"""The languages the lore says a character speaks (#4229 slice 1b): read from the TRAITS lines,
checked like a trait, and kept as DMbot's read beside the sheets. Invented names."""

from __future__ import annotations

import unittest
from collections import Counter

from dmbot.memory import lore_read
from dmbot.memory.models import CONFIRMED
from dmbot.memory.traits_read import Spoken, read_languages, read_traits
from dmbot.ui.name_background import store_languages
from tests.test_memory_store import GUILD_A, MemoryTest

TEXT = (
    "Brewner speaks Dwarvish and Giant. Mira knows the old tongue of Netheril. Vexnir is a duergar."
)


def line(name: str, words: str, quote: str, kind: str = "language") -> str:
    return f'- {name} | {kind} | {words} | quote: "{quote}"'


class TheReader(unittest.TestCase):
    def test_a_line_of_two_is_two_languages_and_a_tongue_keeps_its_words(self) -> None:
        got = read_languages(
            [
                line("Brewner", "Dwarvish and Giant", "Brewner speaks Dwarvish and Giant"),
                line("Mira", "the old tongue of Netheril", "Mira knows the old tongue of Netheril"),
            ],
            TEXT,
            ["Brewner", "Mira"],
        )
        self.assertEqual(
            got,
            [
                Spoken("Brewner", "Dwarvish"),
                Spoken("Brewner", "Giant"),
                Spoken("Mira", "Old tongue of Netheril"),
            ],
        )

    def test_nothing_is_invented(self) -> None:
        counts: Counter[str] = Counter()
        got = read_languages(
            [
                line("Brewner", "Elvish", "Brewner speaks Dwarvish and Giant"),  # not in the quote
                line("Vexnir", "Undercommon", "Vexnir speaks Undercommon"),  # not in the text
                line("Grik", "Goblin", "Brewner speaks Dwarvish and Giant"),  # no such name
            ],
            TEXT,
            ["Brewner", "Vexnir"],
            counts,
        )
        self.assertEqual(got, [])
        self.assertEqual(counts, Counter({"language_not_in_text": 2, "language_no_name": 1}))

    def test_a_name_the_campaign_has_counts(self) -> None:
        got = read_languages(
            [line("Brewner", "Dwarvish", "Brewner speaks Dwarvish")], TEXT, [], existing=["brewner"]
        )
        self.assertEqual(got, [Spoken("Brewner", "Dwarvish")])

    def test_the_traits_reader_leaves_language_lines_alone(self) -> None:
        counts: Counter[str] = Counter()
        got = read_traits(
            [line("Brewner", "Dwarvish", "Brewner speaks Dwarvish")],
            TEXT,
            ["Brewner"],
            lambda _w: None,
            counts,
        )
        self.assertEqual((got, counts), ([], Counter()))

    def test_the_read_is_asked_for_them_and_never_a_guess(self) -> None:
        asked = lore_read.read_instructions()
        self.assertIn("'language' as its kind", asked)
        self.assertIn("Never a guess from a name, a look, a lineage or a homeland", asked)


class Kept(MemoryTest):
    async def test_kept_as_dmbots_read_on_the_one_character_with_that_name(self) -> None:
        brewner = await self.add("Brewner", status=CONFIRMED)
        await self.add("Mira")
        await self.add("Mira", type="place")  # (a place is never given a language)
        twin_a, twin_b = await self.add("Tom"), await self.add("Tom the Younger")
        await self.memory.add_alias(GUILD_A, self.c, twin_b, "Tom", kind="nickname", source="dm")
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        kept = await store_languages(
            self.memory,
            campaign,
            [Spoken("Brewner", "Dwarvish"), Spoken("Mira", "Elvish"), Spoken("Tom", "Orc")],
        )
        self.assertEqual(kept, 2)
        (dwarvish,) = await self.memory.story_languages(GUILD_A, self.c, brewner)
        self.assertEqual((dwarvish.name, dwarvish.given_by), ("Dwarvish", "read"))
        for tom in (twin_a, twin_b):  # two entries share "Tom": DMbot can't tell whose
            self.assertEqual(await self.memory.story_languages(GUILD_A, self.c, tom), [])
