"""The family statements of a lore document, in the shapes the owner's upload used, come out as
`kin_of` with their role, and two of them work out a third (#3238, Part A). Invented text and names.

A statement that names the person only in an earlier sentence ("Okthandom ... Family members
historically vanish ... His elder brother, Vaelrith, vanished ...") is the pronoun reach (#2757);
"son of" and "whose husband" read in one sentence. Pure: no database, no AI (the AI's answer is
written out in the shape the reader gets back)."""

from __future__ import annotations

import json
import unittest
from collections import Counter

from dmbot.memory import background as bg
from dmbot.memory import kin_derive

TEXT = (
    "Corvin Hale hunted wolves in the hills. Old stories say a curse follows the family. "
    "His elder brother, Daric, vanished at forty leaving a camp, while his younger sister, Pell, "
    "now hears the same whispers.\n\n"
    "Dale Ashe, son of Marn, farmed the valley. His sister, Wren, kept the inn.\n\n"
    "Hob Venn lived in Easthaven, whose husband, Tarn Venn, sold lamps."
)
NAMES = bg.reach_names(
    {"corvin hale", "daric", "pell", "dale ashe", "marn", "wren", "hob venn", "tarn venn"}
)


def link(a: str, how: str, b: str, quote: str) -> dict[str, str]:
    return {"a": a, "how": how, "b": b, "quote": quote}


REPLY = json.dumps(
    {
        "connections": [
            link("Corvin Hale", "brother", "Daric", "His elder brother, Daric, vanished at forty"),
            link(
                "Corvin Hale",
                "sister",
                "Pell",
                "while his younger sister, Pell, now hears the same whispers.",
            ),
            link("Dale Ashe", "son", "Marn", "Dale Ashe, son of Marn, farmed the valley."),
            link("Dale Ashe", "sister", "Wren", "His sister, Wren, kept the inn."),
            link("Hob Venn", "husband", "Tarn Venn", "whose husband, Tarn Venn, sold lamps"),
        ]
    }
)


def read() -> tuple[list[bg.Connection], Counter[str]]:
    lost: Counter[str] = Counter()
    return bg.read_connections(REPLY, TEXT, dropped=lost, others=NAMES), lost


class FamilyShapes(unittest.TestCase):
    def test_every_statement_comes_out_as_kin_with_its_role(self) -> None:
        got, lost = read()
        self.assertEqual(
            sorted((c.a, c.words, c.b) for c in got),
            sorted(
                [
                    ("Corvin Hale", "sibling", "Daric"),
                    ("Corvin Hale", "sibling", "Pell"),
                    ("Marn", "parent", "Dale Ashe"),
                    ("Dale Ashe", "sibling", "Wren"),
                    ("Hob Venn", "spouse", "Tarn Venn"),
                ]
            ),
        )
        self.assertTrue(all(c.how == "kin_of" for c in got))
        self.assertEqual(sum(lost.values()) - lost["quote_widened"] - lost["quote_reach"], 0)

    def test_the_statement_two_sentences_after_the_name_needs_the_reach(self) -> None:
        _, lost = read()
        self.assertEqual(lost["quote_reach"], 2)  # the brother and the sister of Corvin Hale
        # (the second relative stands after the first, whose name does not take "his" away)

    def test_each_quote_is_the_text_word_for_word(self) -> None:
        for c in read()[0]:
            self.assertIn(c.quote, TEXT)

    def test_two_stated_links_work_out_the_third(self) -> None:
        got, _ = read()
        ids = {"Marn": "m", "Dale Ashe": "d", "Wren": "w", "Corvin Hale": "c", "Daric": "x"}
        stated = [
            kin_derive.Stated(f"l{i}", ids[c.a], c.words, ids[c.b])
            for i, c in enumerate(got)
            if c.a in ids and c.b in ids
        ]
        derived = kin_derive.derive(stated)
        self.assertEqual([(d.a_id, d.role, d.b_id) for d in derived], [("m", "parent", "w")])
