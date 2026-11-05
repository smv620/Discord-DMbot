"""A catch-all connection's own words can name a third entry (#3153): "swore on Veil's grave to
guard". The gate blanks such a label for a viewer who may not name that entry, by its main name or
by an other name, and leaves it for the DM and for names the viewer knows. Pure: the door answers
are written by hand. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import gate
from dmbot.web import graph_view
from dmbot.web import node_summary_run as run
from tests.test_node_summary_run import KESH, MIRA, PC, RANKS, VEIL, world

LABEL = "swore on Veil's grave to guard"


class ThePureRule(unittest.TestCase):
    def test_a_name_in_the_words_is_found_whole_possessive_case_and_hyphen_aside(self) -> None:
        self.assertTrue(gate.label_names("swore on Veil's grave", ["Lord Veil", "Veil"]))
        self.assertTrue(gate.label_names("SWORE ON VEIL", ["veil"]))
        self.assertTrue(gate.label_names("hates Ka-Zeth", ["Ka'zeth"]))

    def test_the_last_word_of_a_name_names_it_the_way_people_say_it(self) -> None:
        self.assertTrue(gate.label_names("swore on Veil's grave", ["Lord Veil"]))
        self.assertFalse(gate.label_names("swore on an oak", ["Lord Ox"]))  # (too short a word)

    def test_a_part_of_a_word_is_not_a_name(self) -> None:
        self.assertFalse(gate.label_names("swore on Veiled things", ["Veil"]))
        self.assertFalse(gate.label_names("pays in coin", []))
        self.assertFalse(gate.label_names("pays in coin", ["", "  "]))

    def test_the_label_is_blanked_or_kept_whole(self) -> None:
        self.assertEqual(gate.safe_label(LABEL, ["Veil"]), "")
        self.assertEqual(gate.safe_label(LABEL, ["Dorn"]), LABEL)
        self.assertEqual(gate.safe_label("", ["Veil"]), "")


def source(label: str = LABEL) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mira's catch-all connection to Kesh carries the label; Veil is known to no one, with a
    secret other name; Mira and Kesh and the player character are known to the party."""
    src, kn = world([("l1", MIRA, KESH, "associated_with")], {"l1": [PC]})
    src["links"][0]["detail"] = label
    kn["knowers"] = [r for r in kn["knowers"] if r[1] != VEIL]  # (no one knows Veil)
    kn["liveEntries"] = [i for i in kn["liveEntries"] if i != VEIL]
    kn["aliasRows"] = [
        {
            "id": "al1", "entity_id": VEIL, "text": "the Grey Fox", "key": "grey fox",
            "secret": True, "status": "confirmed", "kind": "nickname",
        }
    ]  # fmt: skip
    return src, kn


def detail_for(viewer: gate.Viewer, label: str = LABEL) -> str | None:
    src, kn = source(label)
    graph = graph_view.build(src, kn, viewer)
    edges = [e for e in graph.edges if e["id"] == "l1"]
    return str(edges[0]["detail"]) if edges else None


class OnTheGraph(unittest.TestCase):
    def test_a_player_never_gets_a_label_that_names_an_entry_they_may_not(self) -> None:
        self.assertEqual(detail_for(gate.Viewer.player([PC])), "")

    def test_the_dm_keeps_the_label(self) -> None:
        self.assertEqual(detail_for(gate.Viewer.the_dm()), LABEL)

    def test_a_label_naming_only_known_entries_is_kept(self) -> None:
        self.assertEqual(
            detail_for(gate.Viewer.player([PC]), "taught by Kesh in secret"),
            "taught by Kesh in secret",
        )

    def test_a_label_naming_a_hidden_entry_by_an_other_name_is_blanked(self) -> None:
        self.assertEqual(detail_for(gate.Viewer.player([PC]), "owes the Grey Fox a debt"), "")

    def test_seeing_as_a_character_is_the_same_player_path(self) -> None:
        self.assertEqual(detail_for(gate.Viewer.character(PC)), "")
        self.assertEqual(detail_for(gate.Viewer.the_party()), "")

    def test_a_player_who_knows_the_entry_keeps_the_label(self) -> None:
        src, kn = source()
        kn["knowers"].append([gate.ENTRY, VEIL, "party", 1.0])
        kn["liveEntries"].append(VEIL)
        graph = graph_view.build(src, kn, gate.Viewer.player([PC]))
        (edge,) = [e for e in graph.edges if e["id"] == "l1"]
        self.assertEqual(edge["detail"], LABEL)


class OnTheNodeSummary(unittest.TestCase):
    def test_the_summary_a_player_reads_does_not_carry_the_hidden_name(self) -> None:
        src, kn = source()
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.player([PC]), peek=False)
        assert got is not None
        self.assertNotIn("Veil", got.text)
        self.assertNotIn("Grey Fox", got.text)
        self.assertNotIn("grave", got.text)

    def test_the_dm_summary_still_has_the_words(self) -> None:
        src, kn = source()
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.the_dm(), peek=False)
        assert got is not None
        self.assertIn("grave", got.text)
