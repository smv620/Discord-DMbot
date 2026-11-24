"""The one companion check every path runs before asking the DM about "travels with" (#5164): two
player characters are never asked, a pair named the wrong way round is asked the right way round,
the card says what the words mean, and a refusal says why. Pure: no database, no Discord."""

from __future__ import annotations

import unittest

from dmbot.memory.claims import (
    EXTRACTOR,
    TRAVELS_REASON,
    Claim,
    Extracted,
    says_travels_with,
    turned_companion,
)
from dmbot.memory.link_words import TRAVELS_WITH_MEANS
from dmbot.memory.models import DM, Entity
from dmbot.memory.store import (
    COMPANION_NOT_NPC,
    COMPANION_TARGET,
    companion_order,
    companion_refusal,
)
from dmbot.memory.who_resolve import UnnamedLink
from dmbot.ui import claims_review
from dmbot.ui.name_background import _companions_fit

PC = {"type": "character", "role": "player_character", "party": False}
NPC = {"type": "character", "role": "npc", "party": False}
PARTY = {"type": "faction", "role": None, "party": True}
PLACE = {"type": "place", "role": None, "party": False}


def claim(relationship: str) -> Claim:
    return Claim(
        "c1", "Glyph", relationship, "Cerric", "", "dm_said", ("1",), (7,), "extractor", "p", "m",
        "proposed", "", 1, 1, None, None,
    )  # fmt: skip


class TheOrder(unittest.TestCase):
    def test_a_companion_with_a_player_character_is_kept_as_given(self) -> None:
        self.assertIs(companion_order(NPC, PC, DM), False)

    def test_a_player_character_named_first_is_turned_round(self) -> None:
        self.assertIs(companion_order(PC, NPC, DM), True)

    def test_two_player_characters_are_never_a_companion_link(self) -> None:
        self.assertIsNone(companion_order(PC, PC, DM))
        self.assertIsNone(companion_order(PC, PC, EXTRACTOR))
        self.assertIsNone(companion_order(PC, PC, "background"))

    def test_two_npcs_or_a_place_are_never_one(self) -> None:
        self.assertIsNone(companion_order(NPC, NPC, DM))
        self.assertIsNone(companion_order(NPC, PLACE, DM))
        self.assertIsNone(companion_order(PLACE, PC, DM))

    def test_the_party_is_for_the_dm_and_the_session_reader_never_a_document(self) -> None:
        self.assertIs(companion_order(NPC, PARTY, EXTRACTOR), False)
        self.assertIs(companion_order(PARTY, NPC, DM), True)
        self.assertIsNone(companion_order(NPC, PARTY, "background"))


class TheWords(unittest.TestCase):
    def test_what_travels_with_means(self) -> None:
        self.assertEqual(
            TRAVELS_WITH_MEANS,
            "a companion who goes along, like a hireling, a familiar or a rescued prisoner",
        )

    def test_a_companion_refusal_is_the_rules_one_sentence(self) -> None:
        self.assertEqual(
            companion_refusal(COMPANION_NOT_NPC),
            "Only a character who isn't a player character can be a companion.",
        )
        self.assertEqual(companion_refusal(COMPANION_TARGET), COMPANION_TARGET)

    def test_any_other_refusal_is_not_a_companion_one(self) -> None:
        self.assertIsNone(companion_refusal("Something else went wrong."))

    def test_the_words_of_a_companion_link_either_way_round(self) -> None:
        for words in ("travels with", "Travels With", "is joined by", "accompanies"):
            self.assertTrue(says_travels_with(words), words)
        for words in ("carries", "knows", "travels to", ""):
            self.assertFalse(says_travels_with(words), words)

    def test_the_session_card_says_what_it_means(self) -> None:
        text = claims_review.question_text(claim("travels with"))
        self.assertIn("**Glyph** travels with **Cerric**? _(you said)_", text)
        self.assertIn(
            "_A companion who goes along, like a hireling, a familiar or a rescued prisoner._",
            text,
        )

    def test_any_other_session_card_is_as_it_was(self) -> None:
        self.assertEqual(
            claims_review.question_text(claim("carries")),
            "**Glyph** carries **Cerric**? _(you said)_",
        )


def entity(eid: str, role: str) -> Entity:
    return Entity(eid, "character", eid, "", "confirmed", None, "dm", 1, role=role)


def unnamed(fixed: str, fixed_is_a: bool, what: str = "hireling") -> UnnamedLink:
    return UnnamedLink(fixed, fixed_is_a, "travels_with", "q", "", what, False, "")


class TheTurn(unittest.TestCase):
    said = Extracted("Cerric", "travels with", "Glyph", "", ("1",), "dm_said")

    def test_a_pair_said_the_wrong_way_round_is_asked_turned(self) -> None:
        turned, reason = turned_companion(
            self.said, TRAVELS_REASON, lambda c: "" if c.subject == "Glyph" else TRAVELS_REASON
        )
        self.assertEqual((turned.subject, turned.object, reason), ("Glyph", "Cerric", ""))
        self.assertEqual(turned.relationship, "travels with")

    def test_a_pair_wrong_both_ways_keeps_its_reason(self) -> None:
        turned, reason = turned_companion(self.said, TRAVELS_REASON, lambda c: TRAVELS_REASON)
        self.assertIs(turned, self.said)
        self.assertEqual(reason, TRAVELS_REASON)

    def test_any_other_reason_is_never_turned(self) -> None:
        turned, reason = turned_companion(self.said, "something else", lambda c: "")
        self.assertIs(turned, self.said)
        self.assertEqual(reason, "something else")


class TheUnnamedEnd(unittest.TestCase):
    def fit(self, *links: UnnamedLink) -> list[UnnamedLink]:
        entities = {"pc": entity("pc", "player_character"), "npc": entity("npc", "npc")}
        return _companions_fit(list(links), lambda k: k, entities, None)

    def test_an_unnamed_companion_of_a_player_character_is_kept(self) -> None:
        self.assertEqual(self.fit(unnamed("pc", False)), [unnamed("pc", False)])

    def test_a_player_character_named_first_is_turned(self) -> None:
        self.assertEqual(self.fit(unnamed("pc", True)), [unnamed("pc", False)])

    def test_an_npc_or_a_group_at_either_end_is_left_out(self) -> None:
        self.assertEqual(self.fit(unnamed("npc", False), unnamed("npc", True)), [])
        self.assertEqual(self.fit(unnamed("pc", False, what="the sailors from Waterdeep")), [])

    def test_any_other_kind_passes_untouched(self) -> None:
        other = UnnamedLink("pc", True, "knows", "q", "", "hireling", False, "")
        self.assertEqual(self.fit(other), [other])


if __name__ == "__main__":
    unittest.main()
