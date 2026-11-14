"""The kind of a lore connection held to its own quote (#3780): the sample lore's sentences that
step 10's read got wrong, and the readings each check must leave alone. Pure; the sentences are
the public sample lore's."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from dataclasses import replace

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
        self.assertEqual(got.words, "hostile")  # ("eliminate" is a cue for it, as "kill" is)
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
        self.assertEqual(lost["knows_a_fact"], 1)

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

    def test_real_employment_with_a_hostile_word_nearby_stays_service(self) -> None:
        for a, b, quote in (
            ("Hlin", "Bounty Hunters' Guild", "Hlin works for the Bounty Hunters' Guild"),
            ("Pell", "hunting lodge", "Pell is employed by the hunting lodge"),
            ("Sephek", "Torrga", "Sephek serves Torrga, killing anyone who threatens her"),
            ("Mara", "Zhentarim", "Mara is an assassin who kills for the Zhentarim"),
            ("Sephek", "Torrga", "Torrga hired Sephek to kill her rivals; Sephek is loyal"),
            ("Sephek", "Torrga", "Torrga pays Sephek to kill anyone who crosses Torrga"),
        ):
            made = Connection(a, "serves", b, quote)
            self.assertEqual(run(made)[0], made, quote)

    def test_apprentice_elsewhere_in_the_quote_turns_nothing_round(self) -> None:
        for a, b, quote in (
            ("Norro", "Syllar Sparkwalker", SPIRE),  # (the Keeper, not one of the apprentices)
            ("Hlin", "Torrga", "Hlin, who once trained an apprentice, now works for Torrga"),
            (
                "Syllar",
                "Blackstaff Tower",
                "Wit and Emmerich, the apprentices of Syllar, keep Blackstaff Tower, "
                "which Syllar serves",
            ),
        ):
            made = Connection(a, "serves", b, quote)
            self.assertEqual(run(made)[0], made, quote)

    def test_hired_names_the_employer_first_so_it_is_left_alone(self) -> None:
        for words in ("hired", "employed", "hired to kill"):
            made = Connection("Torrga", FALLBACK_PREDICATE, "Sephek",
                              f"Torrga {words} Sephek", words=words)  # fmt: skip
            self.assertEqual(run(made)[0], made, words)
        got, _ = run(Connection("Sephek", FALLBACK_PREDICATE, "Torrga",
                                "Sephek was hired by Torrga", words="hired by"))  # fmt: skip
        assert got is not None
        self.assertEqual(got.how, "serves")

    def test_genuine_knowing_with_a_fact_word_nearby_stays(self) -> None:
        for quote in (
            "Hlin and Sephek know each other well, though Hlin knows that the guild watches",
            "Hlin has known Sephek for years; she knows how to pick locks",
            "Hlin knows where the Brotherhood's hired agent Sephek hides",
            "Hlin knows that she betrayed him to Sephek",
        ):
            made = Connection("Hlin", "knows", "Sephek", quote)
            self.assertEqual(run(made)[0], made, quote)

    def test_an_owner_word_about_someone_else_leaves_leads(self) -> None:
        quote = "Veron rules the Fishers; the owner of the Blue Clam is his cousin"
        made = Connection("Veron", "leads", "Fishers", quote)
        self.assertEqual(run(made)[0], made)

    def test_an_apposition_moves_only_its_own_place_and_keeps_real_names(self) -> None:
        other = Connection("Lee", "located_in", "Mirabar",
                           "Griz, son of Lee, a dwarf from the Spine, hails from Mirabar",
                           words="comes from")  # fmt: skip
        self.assertEqual(run(other)[0], other)  # (Mirabar is not the place after "from")
        holly = Connection("Lee", "located_in", "Spine of the World",
                           "Holly Ashdown, daughter of Lee, a dwarf from the Spine of the World",
                           words="comes from")  # fmt: skip
        got, _ = run(holly)
        assert got is not None
        self.assertEqual(got.a, "Holly Ashdown")
        sir = replace(holly, quote="Sir Griz, son of Lee, a dwarf from the Spine of the World")
        moved, _ = run(sir)
        assert moved is not None
        self.assertEqual(moved.a, "Griz")

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


class TheWaitingPath(unittest.TestCase):
    def test_a_waiting_line_is_never_dropped_for_knowing_a_fact(self) -> None:
        made = Connection("Hlin", "knows", "Sephek", "It knows where he sleeps")
        self.assertEqual(held(made, Counter(), waiting=True), made)

    def test_an_apprentice_waiting_for_its_pronoun_keeps_track_of_the_missing_end(self) -> None:
        piece = "Master Syllar Sparkwalker trained many. Among his apprentices was Wit."
        reply = json.dumps({"connections": [
            {"a": "Wit", "how": "works for", "b": "Syllar Sparkwalker",
             "quote": "Among his apprentices was Wit."},
        ]})  # fmt: skip
        open_: list[bg.Unsettled] = []
        bg.read_connections(reply, piece, unsettled=open_, others={"wit", "syllar sparkwalker"})
        for u in open_:  # (if the quote waits, its ends and its missing one stay in step)
            missing = u.made.a if u.missing_is_a else u.made.b
            self.assertEqual(missing, "Syllar Sparkwalker")


class ThroughTheReader(unittest.TestCase):
    def test_read_connections_applies_it_and_counts(self) -> None:
        said = "Hlin Trollbane gives the party a contract to capture or eliminate Sephek Kaltro"
        reply = json.dumps({"connections": [
            {"a": "Hlin Trollbane", "how": "works for", "b": "Sephek Kaltro", "quote": said},
        ]})  # fmt: skip
        piece = said + "."
        lost: Counter[str] = Counter()
        (got,) = bg.read_connections(reply, piece, dropped=lost)
        self.assertEqual((got.how, got.words), ("knows", "hostile"))
        self.assertEqual(lost["kind_contract_against"], 1)


# The sample lore's sentences for carrying, owning and finding (#3619): lines 42, 21, 90 and 85.
FIGURINE = (
    "He carries a unique wolf figurine inlaid with black gemstones, etched with Sylvan runes of "
    "conjuration."
)
TOME = "He grabbed his master's locked azure tome and escaped via an emergency teleportation rune"
SOLD = "Claude sold a wolf figurine etched with conjuration runes to Belleros for 75 gold"
VESSEL = "they bypassed the first ship and boarded Fizzlebomb's vessel, the Aurora's Wind"
CARRIES = "Claude Ambere carries a unique wolf figurine"  # (the reader needs both names said)
FINDS = "Claude finds the heart of chardalyn hidden under the bucket lift"


class CarryingIsNotOwning(unittest.TestCase):
    def test_line_42_carries_with_no_belonging_word_is_carries(self) -> None:
        got, lost = run(Connection("Claude Ambere", "owns", "wolf figurine", FIGURINE))
        assert got is not None
        self.assertEqual((got.a, got.how, got.b), ("Claude Ambere", "carries", "wolf figurine"))
        self.assertEqual(lost["kind_carried"], 1)

    def test_line_21_a_possessive_beside_the_carry_word_leaves_the_ai_s_reading(self) -> None:
        made = Connection("Cerric Vanya'elen", "owns", "azure tome", TOME)
        self.assertEqual(run(made), (made, Counter()))  # ("his", "master's": belonging words; the
        # check cannot tell whose thing a possessive names, so it leaves the line alone)

    def test_his_figurine_which_he_carries_stays_owns(self) -> None:
        made = Connection("Claude", "owns", "wolf figurine", "his wolf figurine, which he carries")
        self.assertEqual(run(made)[0], made)

    def test_line_90_no_carry_word_stays_owns(self) -> None:
        for made in (
            Connection("Belleros Stormgrace", "owns", "wolf figurine", SOLD),
            Connection("Fizzlebomb", "owns", "Aurora's Wind", VESSEL),
        ):
            self.assertEqual(run(made)[0], made, made.quote)

    def test_every_belonging_word_keeps_owns(self) -> None:
        for quote in (
            "Mara carries the lantern that belongs to her",
            "Mara bought the lantern and carries it",
            "Mara owns and carries the lantern",
            "Mara wears their lantern",
            "Mara carries the brothers' lantern",
        ):
            made = Connection("Mara", "owns", "lantern", quote)
            self.assertEqual(run(made)[0], made, quote)

    def test_worn_wore_and_carried_out_are_no_carrying(self) -> None:
        for quote in (
            "Mara keeps a well-worn map",
            "the years wore on and Mara kept the map",
            "Mara carried out the theft of the map",
        ):
            made = Connection("Mara", "owns", "map", quote)
            self.assertEqual(run(made)[0], made, quote)
        got, _ = run(Connection("Mara", "owns", "spear", "Mara wields a chardalyn spear"))
        assert got is not None
        self.assertEqual(got.how, "carries")

    def test_only_owns_is_turned(self) -> None:
        made = Connection("Mara", "knows", "Pell", "Mara carries Pell across the river")
        self.assertEqual(run(made)[0], made)


class TheReaderOffersCarryingAndFinding(unittest.TestCase):
    def test_carries_and_found_are_offered_and_read(self) -> None:
        self.assertIn("carries", bg.OFFERED_WORDS)
        self.assertIn("found", bg.OFFERED_WORDS)
        reply = json.dumps({"connections": [
            {"a": "Claude Ambere", "how": "carries", "b": "wolf figurine", "quote": CARRIES},
            {"a": "Claude", "how": "found", "b": "heart of chardalyn", "quote": FINDS},
        ]})  # fmt: skip
        carried, found = bg.read_connections(reply, CARRIES + ". " + FINDS + ".")
        self.assertEqual(carried.how, "carries")
        self.assertEqual((found.how, found.words), (FALLBACK_PREDICATE, "found"))  # (a past fact)

    def test_both_lore_passes_name_them(self) -> None:
        from dmbot.memory import lore_read

        first, second = lore_read.read_instructions(), lore_read.shape_instructions()
        for text in (first, second):
            self.assertIn("carries", text)
            self.assertIn("found = " if text is second else "found (", text)
        # (one line is the default: both only when the text says both)
        self.assertIn("write owns and carries for one pair only when the text says both", first)
        self.assertIn(
            "write two lines, one owns and one carries, only when the text says both", second
        )
