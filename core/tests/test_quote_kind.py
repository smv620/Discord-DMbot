"""The kind of a lore connection held to its own quote (#3780): the sample lore's sentences that
step 10's read got wrong, and the readings each check must leave alone. Pure; the sentences are
the public sample lore's."""

from __future__ import annotations

import json
import unittest
from collections import Counter

from dmbot.memory import background as bg
from dmbot.memory.background import Connection
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.memory.quote_kind import held

HLIN = "She gives the party a contract to capture or eliminate Sephek Kaltro"
SPIRE = (
    "This led to Norro serving as Keeper of the Sparkwalker Spire under Master Syllar Sparkwalker "
    "of Blackstaff Tower, meticulously tending the laboratory and biding his time alongside rival "
    "apprentices Emmerich and Wit."
)
SEPHEK = "A mysterious warrior employed as a bodyguard for Torrga Icevein"
VERON = "A half-orc who works as the owner and proprietor of The Blue Clam inn"
NOTE = '"Danneth, Easthaven. The Brotherhood knows who you are. Take precautions, trust no one!'
GRIZ = "Originally Griz, son of Lee, an Ursine warrior from the Spine of the World, Claude erased"


def run(made: Connection) -> tuple[Connection | None, Counter[str]]:
    lost: Counter[str] = Counter()
    return held(made, lost), lost


class TheReadingsTheQuoteRulesOut(unittest.TestCase):
    def test_a_contract_to_eliminate_someone_is_hostile_not_service(self) -> None:
        got, lost = run(Connection("Hlin Trollbane", "serves", "Sephek Kaltro", HLIN))
        assert got is not None
        self.assertEqual((got.a, got.how, got.b), ("Hlin Trollbane", "knows", "Sephek Kaltro"))
        self.assertEqual(lost["kind_contract_against"], 1)

    def test_an_apprentice_who_serves_is_mentored_by_the_master(self) -> None:
        got, _ = run(Connection("Wit", "serves", "Syllar Sparkwalker", SPIRE))
        assert got is not None
        self.assertEqual((got.a, got.how, got.b), ("Syllar Sparkwalker", "mentor_of", "Wit"))

    def test_the_words_bodyguard_for_are_service(self) -> None:
        made = Connection("Sephek Kaltro", FALLBACK_PREDICATE, "Torrga Icevein", SEPHEK,
                          words="bodyguard for")  # fmt: skip
        got, _ = run(made)
        assert got is not None
        self.assertEqual((got.how, got.words), ("serves", ""))

    def test_the_owner_of_an_inn_owns_it(self) -> None:
        got, _ = run(Connection("Veron Braig", "leads", "The Blue Clam", VERON))
        assert got is not None
        self.assertEqual(got.how, "owns")

    def test_knowing_who_someone_is_is_no_tie_to_another_name_in_the_note(self) -> None:
        got, lost = run(Connection("Brotherhood", "knows", "Danneth", NOTE))
        self.assertIsNone(got)
        self.assertEqual(lost["kind_knows_a_fact"], 1)

    def test_an_origin_in_an_apposition_belongs_to_the_first_name(self) -> None:
        made = Connection("Lee", "located_in", "Spine of the World", GRIZ, words="comes from")
        got, _ = run(made)
        assert got is not None
        self.assertEqual((got.a, got.b, got.words), ("Griz", "Spine of the World", "comes from"))


class WhatEachCheckLeavesAlone(unittest.TestCase):
    def test_plain_service_mentoring_leading_and_knowing_stay(self) -> None:
        for made in (
            Connection("Sephek Kaltro", "serves", "Torrga Icevein", SEPHEK),
            Connection("Torrga Icevein", "leads", "Torg's Caravan",
                       "A dwarf merchant and leader of Torg's Caravan"),
            Connection("Cerric", "knows", "Belleros", "Cerric knows Belleros from the road"),
            Connection("Hlin Trollbane", "located_in", "Lonelywood",
                       "Hlin operating out of Lonelywood", words="works in"),
            Connection("Cerric Vanya'elen", "rival_of", "Wit", SPIRE),
        ):  # fmt: skip
            got, lost = run(made)
            self.assertEqual(got, made, made)
            self.assertEqual(sum(lost.values()), 0, made)

    def test_knowing_a_fact_that_names_the_other_end_stays(self) -> None:
        for quote in (
            "Hlin knows that Sephek killed again",
            "Hlin knows Sephek, and knows that he lies",
        ):
            made = Connection("Hlin", "knows", "Sephek", quote)
            self.assertEqual(run(made)[0], made, quote)

    def test_an_owner_who_also_leads_keeps_leads(self) -> None:
        made = Connection("Veron", "leads", "The Crew", "Veron, owner and captain of The Crew")
        self.assertEqual(run(made)[0], made)


class ThroughTheReader(unittest.TestCase):
    def test_read_connections_applies_it_and_counts(self) -> None:
        said = "Hlin Trollbane gives the party a contract to capture or eliminate Sephek Kaltro"
        reply = json.dumps({"connections": [
            {"a": "Hlin Trollbane", "how": "works for", "b": "Sephek Kaltro", "quote": said},
        ]})  # fmt: skip
        piece = said + "."
        lost: Counter[str] = Counter()
        (got,) = bg.read_connections(reply, piece, dropped=lost)
        self.assertEqual(got.how, "knows")
        self.assertEqual(lost["kind_contract_against"], 1)
