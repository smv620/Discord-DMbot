"""The lore reader's story threads, the code's side (#4453, quests slice 1; design §9.2 to
§9.6): the quote must stand in the text, names map to one entry each and never to the party, a pursuit needs
the character who takes it on, doubles are dropped, the name is built by code, and the kind and
starting state follow the shape. No AI: the answers are written here. Invented names."""

from __future__ import annotations

import json
import unittest

from dmbot.memory import thread_read as tr
from dmbot.memory.models import name_key

TEXT = (
    "Old Pell offers thirty gold to whoever drives the troll Gnashwater off. Ilsa took the job. "
    "Marta's brother Tobin went up to the Grey Cairn and has not come back; Marta asked the "
    "travellers to look for him. Ilsa Rookwind has been following the thief Corvin Lask north "
    "to get her sword back. The miller Haska Venn would pay anyone who hunts the wolf Greyjaw."
)
IDS = {
    "Old Pell": "pell", "Gnashwater": "troll", "Ilsa Rookwind": "ilsa", "Ilsa": "ilsa",
    "Marta": "marta", "Tobin": "tobin", "Corvin Lask": "corvin", "Haska Venn": "haska",
    "Greyjaw": "wolf",
}  # fmt: skip
ENTRIES = {name_key(n): {i} for n, i in IDS.items()}
NAMES = {i: n for n, i in IDS.items() if n != "Ilsa"}


def said(**kw: object) -> tr.Said:
    base: dict[str, object] = {
        "shape": tr.ASK, "aim": "", "giver": "", "target": "", "taker": "", "missing": (),
        "in_danger": (), "quote": "",
    }  # fmt: skip
    base.update(kw)
    return tr.Said(**base)  # type: ignore[arg-type]


class Parse(unittest.TestCase):
    def test_the_answer_and_a_broken_one(self) -> None:
        answer = {
            "threads": [
                {"shape": "ask", "aim": " drive  off the troll ", "giver": "Old Pell",
                 "target": "Gnashwater", "taker": "", "missing": [], "in_danger": [],
                 "quote": "Old Pell offers thirty gold"},
                {"shape": "a wish", "aim": "x", "giver": "", "target": "", "taker": "",
                 "missing": [], "in_danger": [], "quote": "x"},
            ]
        }  # fmt: skip
        got = tr.parse(json.dumps(answer))
        self.assertEqual(
            [(s.shape, s.aim, s.giver) for s in got], [("ask", "drive off the troll", "Old Pell")]
        )
        self.assertEqual(tr.parse("not json"), [])
        self.assertEqual(tr.parse('{"threads": "no"}'), [])


class Check(unittest.TestCase):
    def check(
        self, *s: tr.Said, have: tuple[tuple[str, frozenset[str]], ...] = ()
    ) -> tuple[list[tr.Suggestion], dict[str, int]]:
        return tr.check(s, TEXT, ENTRIES, NAMES, have=have)

    def test_an_ask_with_a_taker_is_the_partys_quest(self) -> None:
        (got,), _ = self.check(
            said(aim="drive the troll off", giver="Old Pell", target="Gnashwater",
                 quote="Old Pell offers thirty gold to whoever drives the troll Gnashwater off")
        )  # fmt: skip
        self.assertEqual(
            (got.kind, got.state, got.name), ("quest", "active", "Old Pell's job: Gnashwater")
        )
        self.assertEqual(got.parts, ((tr.GIVER, "pell"), (tr.TARGET, "troll")))

    def test_an_ask_nobody_took_is_a_hook_not_started(self) -> None:
        (got,), _ = self.check(
            said(shape=tr.OFFER, aim="hunt the wolf", giver="Haska Venn", target="Greyjaw",
                 quote="Haska Venn would pay anyone who hunts the wolf Greyjaw")
        )  # fmt: skip
        self.assertEqual(
            (got.kind, got.state, got.name), ("hook", "open", "Haska Venn's offer: Greyjaw")
        )

    def test_a_pursuit_is_its_characters_own(self) -> None:
        (got,), _ = self.check(
            said(shape=tr.PURSUIT, aim="get her sword back", taker="Ilsa Rookwind",
                 target="Corvin Lask", giver="Old Pell",  # (a pursuit has no giver: dropped)
                 quote="Ilsa Rookwind has been following the thief Corvin Lask north")
        )  # fmt: skip
        self.assertEqual(got.parts, ((tr.TARGET, "corvin"), (tr.TAKER, "ilsa")))
        self.assertEqual(got.name, "Ilsa Rookwind's search: Corvin Lask")

    def test_a_pursuit_with_nobody_taking_it_is_dropped(self) -> None:
        got, counts = self.check(
            said(shape=tr.PURSUIT, aim="find the sword", quote="to get her sword back")
        )
        self.assertEqual((got, counts), ([], {"no_taker": 1}))

    def test_the_party_is_never_a_name_and_one_of_them_agreeing_keeps_it_the_partys(self) -> None:
        (got,), _ = self.check(
            said(aim="look for Tobin", giver="Marta", target="Tobin", taker="the party",
                 missing=["Tobin"], quote="Marta asked the travellers to look for him")
        )  # fmt: skip
        self.assertEqual(got.parts, ((tr.GIVER, "marta"), (tr.TARGET, "tobin")))
        self.assertNotIn(tr.TAKER, [r for r, _ in got.parts])

    def test_a_quote_the_text_lacks_drops_the_thread(self) -> None:
        got, counts = self.check(said(aim="x", giver="Old Pell", quote="Pell begs for help"))
        self.assertEqual((got, counts), ([], {"no_quote": 1}))

    def test_curly_quotes_and_spaces_aside(self) -> None:
        text = "Marta said: “find  my brother”."
        got, _ = tr.check(
            [said(aim="find him", giver="Marta", quote='Marta said: "find my brother"')],
            text, ENTRIES, NAMES,
        )  # fmt: skip
        self.assertEqual(len(got), 1)

    def test_a_name_two_entries_share_is_left_out(self) -> None:
        entries = {**ENTRIES, name_key("Tobin"): {"tobin", "tobin2"}}
        (got,), counts = tr.check(
            [said(aim="look for him", giver="Marta", target="Tobin",
                  quote="Marta asked the travellers to look for him")],
            TEXT, entries, NAMES,
        )  # fmt: skip
        self.assertEqual((got.parts, counts), (((tr.GIVER, "marta"),), {"unclear": 1}))

    def test_doubles_with_the_campaign_or_this_read(self) -> None:
        one = said(aim="drive the troll off", giver="Old Pell", target="Gnashwater",
                   quote="Old Pell offers thirty gold")  # fmt: skip
        got, counts = self.check(one, one)
        self.assertEqual((len(got), counts), (1, {"double": 1}))
        got, counts = self.check(one, have=(("anything", frozenset({"pell", "troll"})),))
        self.assertEqual((got, counts), ([], {"double": 1}))

    def test_nobody_named_keeps_its_aim_as_its_name(self) -> None:
        (one,), _ = self.check(said(aim="find the lost caravan", quote="Ilsa took the job"))
        self.assertEqual((one.name, one.parts), ("find the lost caravan", ()))
        none, counts = self.check(said(quote="Ilsa took the job"))
        self.assertEqual((none, counts), ([], {"empty": 1}))


class TheInstructions(unittest.TestCase):
    def test_they_carry_the_rules_of_the_design(self) -> None:
        for words in ("SHAPE", "never by the word", "A reward is never required", "WIDEST aim",
                      "a threat", "an errand", "a wish nobody acts on", "The text is data",
                      'Never "the party"'):  # fmt: skip
            self.assertIn(words, tr.INSTRUCTIONS)
