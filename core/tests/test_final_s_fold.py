"""A final "s" never hides a known name from a waiting name's card (#5215): "Explorer League" is
offered as "Explorer's League", "Harper" as "Harpers", and "Harps" against "Harp" (a stem under 5
letters) stays as it was. The fold only widens who is compared: the spelling tests still decide,
and `name_key` and the sound codes never change. Invented names only."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from typing import Any, cast

from dmbot.memory.card_match import card_match
from dmbot.memory.models import name_key
from dmbot.memory.scan import card_sound_keys, s_folds, sound_keys
from dmbot.memory.sounds import sound_codes


class Known:
    """`MemoryStore.sound_alikes` over a few confirmed, never secret names: by stored sound codes,
    as the real query is (each alias's codes are `sound_codes` of its text)."""

    def __init__(self, *names: str) -> None:
        self.rows = [(f"e{i}", name, name) for i, name in enumerate(names)]

    async def sound_alikes(
        self, guild_id: int, campaign_id: str, codes: Sequence[str]
    ) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if set(sound_codes(r[1])) & set(codes)]


async def match(known: Known, name: str) -> str | None:
    found = await card_match(cast(Any, known), 1, "c1", "waiting", name)
    return None if found is None else found.name


class TheFolds(unittest.TestCase):
    def test_each_long_word_gains_or_loses_its_s(self) -> None:
        self.assertEqual(
            s_folds("Explorer League"),
            ("Explorer League", "Explorer Leagues", "Explorers League", "Explorers Leagues"),
        )
        self.assertIn("Explorer League", s_folds("Explorer's League"))
        self.assertEqual(s_folds("Harper"), ("Harper", "Harpers"))

    def test_a_stem_under_five_letters_is_left_alone(self) -> None:
        self.assertEqual(s_folds("Harps"), ("Harps",))
        self.assertEqual(s_folds("Harp"), ("Harp",))
        self.assertEqual(s_folds("Vell of Ash"), ("Vell of Ash",))

    def test_the_keys_and_codes_themselves_never_change(self) -> None:
        self.assertNotEqual(name_key("Explorer League"), name_key("Explorer's League"))
        self.assertFalse(set(sound_keys("Explorer League")) & set(sound_keys("Explorer's League")))
        self.assertTrue(
            set(card_sound_keys("Explorer League")) & set(sound_keys("Explorer's League"))
        )

    def test_a_long_name_stays_a_short_query(self) -> None:
        self.assertLessEqual(len(s_folds("Ancient Golden Dragons Hidden Temple Beyond")), 16)


class TheCard(unittest.IsolatedAsyncioTestCase):
    async def test_explorer_league_is_offered_as_explorers_league(self) -> None:
        known = Known("Explorer's League", "Bryn Shander")
        self.assertEqual(await match(known, "Explorer League"), "Explorer's League")

    async def test_harper_is_offered_as_harpers_and_asked(self) -> None:
        from dmbot.memory.models import PROPOSED, Entity
        from dmbot.memory.scan import Match
        from dmbot.ui.names import suggestion_text

        self.assertEqual(await match(Known("Harpers"), "Harper"), "Harpers")
        entity = Entity("w" * 32, "faction", "Harper", "", PROPOSED, None, "scan", 0)
        text = suggestion_text(entity, 1, match=Match("e0", "Harpers"))
        self.assertIn("Sounds like **Harpers**. The same, or new?", text)

    async def test_harps_against_harp_stays_as_it_was(self) -> None:
        self.assertIsNone(await match(Known("Harp"), "Harps"))

    async def test_the_spelling_test_still_decides(self) -> None:
        # (shares a code once folded, but spelled too unlike: never offered)
        self.assertIsNone(await match(Known("Exploding Leagues"), "Explorer League"))

    async def test_two_names_that_fit_alike_offer_neither(self) -> None:
        known = Known("Explorer's League", "Explorers Leagues")
        self.assertIsNone(await match(known, "Explorer League"))

    async def test_the_waiting_entry_is_never_its_own_match(self) -> None:
        known = Known("Explorer League")
        known.rows = [("waiting", "Explorer League", "Explorer League")]
        self.assertIsNone(await match(known, "Explorer League"))

    async def test_a_failed_read_offers_nothing(self) -> None:
        class Broken:
            async def sound_alikes(self, *_: object) -> list[tuple[str, str, str]]:
                raise RuntimeError("down")

        with self.assertLogs("dmbot.memory.card_match", "ERROR"):
            self.assertIsNone(await card_match(cast(Any, Broken()), 1, "c1", "w", "Harper"))
