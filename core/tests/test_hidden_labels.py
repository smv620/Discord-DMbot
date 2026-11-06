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

    def test_variants_of_a_name_are_found_too(self) -> None:
        for label in ("a Veil-bound oath", "Veils' grave", "swore on Veil", "owes Veil a debt"):
            with self.subTest(label):
                self.assertTrue(gate.label_names(label, ["Veil the Grey"]), label)
        self.assertTrue(gate.label_names("Mira swore", ["Mira Veil"]))  # (the first word, too)

    def test_a_word_a_shown_name_also_has_never_counts(self) -> None:
        self.assertFalse(gate.label_names("served the lord well", ["Lord Veil"], ["Lord Dorn"]))
        self.assertTrue(gate.label_names("served Veil well", ["Lord Veil"], ["Lord Dorn"]))

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


class OnTheGraphEverywhere(unittest.TestCase):
    """Names the source door never sends (a merged entry's old name, a quest) and every kind of
    other name (#3153, review)."""

    def build(self, mutate: Any, label: str) -> str | None:
        src, kn = source(label)
        mutate(src, kn)
        graph = graph_view.build(src, kn, gate.Viewer.player([PC]))
        edges = [e for e in graph.edges if e["id"] == "l1"]
        return str(edges[0]["detail"]) if edges else None

    def test_a_name_only_the_knowers_door_has_is_still_forbidden(self) -> None:
        def add_quest(src: dict[str, Any], kn: dict[str, Any]) -> None:
            kn["entities"].append(entity_row("quest1", "The Ember Quest"))  # (not in src names)

        self.assertEqual(self.build(add_quest, "failed the Ember Quest"), "")

    def test_the_old_name_of_a_merged_entry_is_forbidden_unless_it_lives_on(self) -> None:
        def merged(src: dict[str, Any], kn: dict[str, Any]) -> None:
            old = entity_row("old1", "Brann Stoneheart")
            old.update(status="merged", merged_into=VEIL)
            kn["entities"].append(old)

        self.assertEqual(self.build(merged, "feared Stoneheart"), "")

    def test_a_proposed_other_name_is_not_yet_public(self) -> None:
        def alias(src: dict[str, Any], kn: dict[str, Any]) -> None:
            kn["aliasRows"].append(
                {"id": "al2", "entity_id": KESH, "text": "Ashen Crow", "key": "ashen crow",
                 "secret": False, "status": "proposed", "kind": "nickname"}
            )  # fmt: skip

        self.assertEqual(self.build(alias, "betrayed by the Ashen Crow"), "")

    def test_a_secret_name_the_viewer_holds_is_kept(self) -> None:
        def held(src: dict[str, Any], kn: dict[str, Any]) -> None:
            kn["knowers"].append([gate.ALIAS, "al1", "party", 1.0])
            kn["aliases"] = {"al1": [VEIL]}

        self.assertEqual(self.build(held, "owes the Grey Fox a debt"), "owes the Grey Fox a debt")


def entity_row(entity_id: str, name: str) -> dict[str, Any]:
    from tests.test_node_summary_run import entity

    return entity(entity_id, name)


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
