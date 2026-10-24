"""The sentence around a name from the DM's text, held in memory for the name card (#2561).
Names and sentences here are made up."""

from __future__ import annotations

import unittest

from dmbot.memory.lore_context import CONTEXT_MAX, HOLD_MAX, HOLD_S, Held, around

TEXT = (
    "The abbey stands on a cliff. Brother Aldric, the abbot of Greyfen Abbey, secretly pays "
    "the Ashen Hand. Nobody knows why.\n\n# Places\n- Wyrmhold: a ruined fort\n- Dunmere\n"
)


class Around(unittest.TestCase):
    def test_it_gives_the_whole_sentence_with_the_quote(self) -> None:
        got = around(TEXT, "secretly pays the Ashen Hand", "Ashen Hand")
        self.assertEqual(
            got,
            "Brother Aldric, the abbot of Greyfen Abbey, secretly pays the Ashen Hand.",
        )

    def test_it_falls_back_to_the_name_when_the_quote_is_not_there(self) -> None:
        self.assertIn("Greyfen Abbey", around(TEXT, "words that are not in it", "Greyfen Abbey"))

    def test_a_list_item_is_its_own_line(self) -> None:
        self.assertEqual(around(TEXT, "", "Dunmere"), "- Dunmere")
        self.assertEqual(around(TEXT, "", "Wyrmhold"), "- Wyrmhold: a ruined fort")

    def test_nothing_found_is_nothing(self) -> None:
        self.assertEqual(around(TEXT, "no such quote", "Nowhere"), "")
        self.assertEqual(around(TEXT, "", ""), "")

    def test_a_long_sentence_is_cut_around_the_name(self) -> None:
        text = "word " * 200 + "Kazeth the wanderer " + "word " * 200 + "."
        got = around(text, "", "Kazeth")
        self.assertIn("Kazeth", got)
        self.assertLessEqual(len(got), CONTEXT_MAX + 2)
        self.assertTrue(got.startswith("…") and got.endswith("…"))

    def test_a_name_is_a_whole_word_and_a_quote_may_span_a_line_break(self) -> None:
        text = "An annual feast. Ann met Bo.\nHe said hello to\nBrother   Aldric today."
        self.assertEqual(around(text, "", "Ann"), "Ann met Bo.")
        self.assertEqual(
            around(text, "hello to Brother Aldric", "x"), "He said hello to Brother Aldric today."
        )

    def test_the_match_may_differ_in_case(self) -> None:
        self.assertIn("Wyrmhold", around(TEXT, "", "wyrmhold"))


class HeldTest(unittest.TestCase):
    def test_a_sentence_is_held_for_the_review_and_then_goes(self) -> None:
        now = [0.0]
        held = Held(lambda: now[0])
        held.put(1, "e1", "A sentence.")
        self.assertEqual(held.get(1, "e1"), "A sentence.")
        self.assertEqual(held.get(2, "e1"), "")  # never across servers
        now[0] = HOLD_S + 1
        self.assertEqual(held.get(1, "e1"), "")

    def test_it_holds_a_bounded_number(self) -> None:
        held = Held()
        for n in range(HOLD_MAX + 5):
            held.put(1, f"e{n}", "x")
        self.assertEqual(held.get(1, "e0"), "")
        self.assertEqual(held.get(1, f"e{HOLD_MAX + 4}"), "x")

    def test_an_empty_sentence_is_not_held(self) -> None:
        held = Held()
        held.put(1, "e1", "")
        self.assertEqual(held.get(1, "e1"), "")

    def test_expired_sentences_are_freed_even_when_nobody_puts_more(self) -> None:
        now = [0.0]
        held = Held(lambda: now[0])
        held.put(1, "e1", "x")
        now[0] = HOLD_S + 1
        held.get(1, "other")
        self.assertEqual(len(held._items), 0)

    def test_a_decided_name_is_forgotten(self) -> None:
        held = Held()
        held.put(1, "e1", "x")
        held.forget(1, "e1")
        self.assertEqual(held.get(1, "e1"), "")


if __name__ == "__main__":
    unittest.main()
