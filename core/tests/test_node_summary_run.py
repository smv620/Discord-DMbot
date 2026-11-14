"""The click on a node (#3236 slice 2b): the adapter turns the graph a person may see into the
summary, and never lets a name, a connection, a rank or a score reach the wrong person. Pure: the
two source doors' answers are written by hand. Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar
from unittest.mock import patch

from dmbot.memory import gate, node_summary
from dmbot.web import node_summary_run as run

MIRA, KESH, DORN, VEIL = "mira", "kesh", "dorn", "veil"
PC = "pc1"  # (a player character)


def entity(i: str, name: str, kind: str = "character") -> dict[str, Any]:
    return {
        "id": i, "name": name, "type": kind, "status": "confirmed", "merged_into": None,
        "unnamed": False, "unnamed_anchor": None, "unnamed_role": None, "unnamed_what": None,
        "role": None, "hidden_from_players": False, "audience_limited": False,
    }  # fmt: skip


def world(
    links: list[tuple[str, str, str, str]], knowers: dict[str, list[str]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """`links`: (id, a, b, predicate). `knowers`: connection id -> the characters who know it.
    Every entry is known to the party."""
    ids = [MIRA, KESH, DORN, VEIL, PC]
    names = {MIRA: "Mira Voss", KESH: "Kesh", DORN: "Dorn", VEIL: "Lord Veil", PC: "Cerric"}
    src = {
        "names": [{"id": i, "kind": "character", "name": names[i]} for i in ids],
        "links": [
            {
                "id": lid, "a": a, "b": b, "predicate": p, "detail": "", "directional": False,
                "secret": False, "at": 10, "ended": False, "plan": False, "via": None,
            }
            for lid, a, b, p in links
        ],
        "visibility": "peek",
        "isDm": True,
    }  # fmt: skip
    rows: list[list[Any]] = [[gate.ENTRY, i, "party", 1.0] for i in ids]
    for lid, who in knowers.items():
        rows += [[gate.CONNECTION, lid, c, 1.0] for c in who]
    kn = {
        "liveEntries": ids,
        "states": {},
        "connections": {lid: [a, b] for lid, a, b, _ in links},
        "aliases": {},
        "knowers": rows,
        "groups": [[PC, "party"]],
        "playerCharacters": [PC],
        "entities": [entity(i, names[i]) for i in ids],
        "aliasRows": [],
        "learned": [],
    }
    return src, kn


RANKS = {MIRA: 4, KESH: 3, DORN: 2, VEIL: 5, PC: 5}


class TheDm(unittest.TestCase):
    def test_the_dm_ignores_the_peek_and_is_never_gated(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {})
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.the_dm(), peek=False)
        assert got is not None and not got.needs_peek and got.text != ""

    def test_the_dm_sees_everything_at_minor_or_above(self) -> None:
        src, kn = world(
            [("l1", MIRA, KESH, "mentor_of"), ("l2", MIRA, VEIL, "knows")], {"l1": [PC]}
        )
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.the_dm(), peek=False)
        assert got is not None
        assert got.version == "dm" and not got.needs_peek
        assert "Kesh" in got.text and "Lord Veil" in got.text


class ThePlayer(unittest.TestCase):
    def test_a_player_never_gets_a_connection_no_player_character_knows(self) -> None:
        src, kn = world(
            [("l1", MIRA, KESH, "mentor_of"), ("l2", MIRA, VEIL, "knows")], {"l1": [PC]}
        )
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.player([PC]), peek=False)
        assert got is not None
        assert got.version == "players"
        assert "Kesh" in got.text
        assert "Veil" not in got.text  # (no character knows that tie)

    def test_the_rank_and_the_score_are_never_in_a_players_text(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": [PC]})
        viewer = gate.Viewer.player([PC])
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=viewer, peek=False)
        assert got is not None
        high = run.summary_for(src, kn, {**RANKS, KESH: 5}, MIRA, clicker=viewer, peek=False)
        assert high is not None and high.text == got.text  # (a higher rank changes no word)
        assert not any(ch.isdigit() for ch in got.text)

    def test_a_node_the_player_cannot_see_is_not_found(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": [PC]})
        kn["knowers"] = [r for r in kn["knowers"] if r[1] != VEIL]  # (no one knows Lord Veil)
        got = run.summary_for(src, kn, RANKS, VEIL, clicker=gate.Viewer.player([PC]), peek=False)
        assert got is None

    def test_a_neighbour_below_minor_is_left_out_for_a_player_too(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": [PC]})
        got = run.summary_for(
            src, kn, {**RANKS, KESH: 0}, MIRA, clicker=gate.Viewer.player([PC]), peek=False
        )
        assert got is not None
        assert "Kesh" not in got.text

    def test_a_fact_another_character_holds_shuts_the_gate_until_peeked(self) -> None:
        # the party knows the tie (the other PC does); the one who clicked has no such character
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": ["pc2"]})
        kn["playerCharacters"] = [PC, "pc2"]
        kn["groups"] = [[PC, "party"], ["pc2", "party"]]
        src["names"].append({"id": "pc2", "kind": "character", "name": "Brann"})
        kn["liveEntries"].append("pc2")
        kn["entities"].append(entity("pc2", "Brann"))
        kn["knowers"].append([gate.ENTRY, "pc2", "party", 1.0])
        shut = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.player([PC]), peek=False)
        assert shut is not None and shut.needs_peek and shut.text == ""
        opened = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.player([PC]), peek=True)
        assert opened is not None and "Kesh" in opened.text


class TheRules(unittest.TestCase):
    def two_players(self, who: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": who})
        kn["playerCharacters"] = [PC, "pc2"]
        kn["groups"] = [[PC, "party"], ["pc2", "party"]]
        src["names"].append({"id": "pc2", "kind": "character", "name": "Brann"})
        kn["liveEntries"].append("pc2")
        kn["entities"].append(entity("pc2", "Brann"))
        kn["knowers"].append([gate.ENTRY, "pc2", "party", 1.0])
        return src, kn

    def test_a_closed_gate_sends_no_text_and_no_count(self) -> None:
        src, kn = self.two_players(["pc2"])
        viewer = gate.Viewer.player([PC])
        with patch.object(node_summary, "MAX_NEIGHBOURS", 0):  # (so one neighbour is "left out")
            shut = run.summary_for(src, kn, RANKS, MIRA, clicker=viewer, peek=False)
            opened = run.summary_for(src, kn, RANKS, MIRA, clicker=viewer, peek=True)
        assert shut is not None and (shut.text, shut.more) == ("", 0)
        assert opened is not None and opened.text != "" and opened.more == 1

    def test_a_connection_no_player_knows_changes_nothing_a_player_reads(self) -> None:
        links = [("l1", MIRA, KESH, "mentor_of")]
        plain_src, plain_kn = world(links, {"l1": [PC]})
        more_src, more_kn = world(
            [*links, ("l2", KESH, VEIL, "knows"), ("l3", KESH, DORN, "knows")], {"l1": [PC]}
        )
        viewer = gate.Viewer.player([PC])
        one = run.summary_for(plain_src, plain_kn, RANKS, MIRA, clicker=viewer, peek=False)
        two = run.summary_for(more_src, more_kn, RANKS, MIRA, clicker=viewer, peek=False)
        assert one is not None and two is not None
        assert (one.text, one.more) == (two.text, two.more)

    def test_the_dm_ignores_the_peek_and_is_never_gated(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {})
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.the_dm(), peek=False)
        assert got is not None and not got.needs_peek and got.text != ""

    def test_a_player_with_no_characters_sees_nothing_of_it(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": [PC]})
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.player([]), peek=False)
        assert got is not None and got.needs_peek and got.text == ""

    def test_an_other_names_node_has_no_summary(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": [PC]})
        dm = gate.Viewer.the_dm()
        assert run.summary_for(src, kn, RANKS, "alias:x", clicker=dm, peek=False) is None

    def test_a_connection_time_sent_as_text_does_not_break_the_click(self) -> None:
        src, kn = world([("l1", MIRA, KESH, "mentor_of")], {"l1": [PC]})
        src["links"][0]["at"] = "2026-10-30T12:00:00+00:00"
        got = run.summary_for(src, kn, RANKS, MIRA, clicker=gate.Viewer.the_dm(), peek=False)
        assert got is not None and "Kesh" in got.text
        assert run._when("not a time") is None and run._when(None) is None
        assert run._when(7) == 7

    def test_the_second_hop_names_the_best_entry_not_the_first_by_id(self) -> None:
        links = [
            ("l0", MIRA, KESH, "knows"),
            ("f0", KESH, DORN, "knows"),
            ("f1", KESH, VEIL, "knows"),
        ]
        src, kn = world(links, {lid: [PC] for lid, *_ in links})
        ranks = {**RANKS, DORN: 1, VEIL: 4}
        got = run.summary_for(src, kn, ranks, MIRA, clicker=gate.Viewer.player([PC]), peek=False)
        assert got is not None and "through them Lord Veil" in got.text


class TheRankInputs(unittest.TestCase):
    INPUTS: ClassVar[dict[str, Any]] = {
        "entities": [
            {"id": "pc", "type": "character", "role": "player_character", "status": "confirmed",
             "mergedInto": None},
            {"id": "ally", "type": "character", "role": "npc", "status": "confirmed",
             "mergedInto": None},
            {"id": "old", "type": "character", "role": "npc", "status": "merged",
             "mergedInto": "ally"},
        ],
        "relations": [["ally", "pc", "travels_with"]],
        "assessed": [["old", 3, 4], ["old", 1, 2]],
        "heard": [["old", 100], ["ally", 200], ["ally", 200]],
    }  # fmt: skip

    def test_the_door_answer_gives_the_same_ranks_as_the_bots_reader(self) -> None:
        ranks = run.ranks_of(self.INPUTS)
        assert ranks["pc"] == 5
        assert ranks["ally"] == 4  # (the merged twin's newest assessment stands for it)
        assert "old" not in ranks

    def test_a_companion_and_two_sessions_floor_without_an_assessment(self) -> None:
        bare = {**self.INPUTS, "assessed": []}
        ranks = run.ranks_of(bare)
        assert ranks["ally"] >= 3  # (a player character's companion, and heard in two sessions)


class TheDeeds(unittest.TestCase):
    """A recorded deed is `doer —did→ deed —affected→ viewer` (#1732): the summary reads it as one
    fact between the doer and each viewer and never names the deed entry (#3236 slice 3)."""

    def deed_world(
        self, kind: str, effect: str = "Helped them"
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        src, kn = world([("l1", PC, VEIL, "did"), ("l2", VEIL, KESH, "affected")], {})
        for link in src["links"]:
            link["secret"] = True
            link["detail"] = kind if link["id"] == "l1" else effect
        for item in src["names"]:
            if item["id"] == VEIL:
                item["kind"] = "deed"
                item["name"] = "Saved the mill"
        for row in kn["entities"]:
            if row["id"] == VEIL:
                row["name"], row["type"] = "Saved the mill", "deed"
        return src, kn

    def text(self, src: dict[str, Any], kn: dict[str, Any], node: str) -> str:
        got = run.summary_for(src, kn, RANKS, node, clicker=gate.Viewer.the_dm(), peek=False)
        assert got is not None
        return got.text

    def test_the_doer_and_the_viewer_read_one_fact_each_way(self) -> None:
        src, kn = self.deed_world("rescue")
        self.assertIn("rescued by them", self.text(src, kn, PC))  # (said of Kesh about Cerric)
        self.assertIn("rescued them", self.text(src, kn, KESH))

    def test_the_deed_entry_is_never_named_as_a_neighbour(self) -> None:
        src, kn = self.deed_world("rescue")
        self.assertNotIn("Saved the mill", self.text(src, kn, PC))
        self.assertNotIn("Saved the mill", self.text(src, kn, KESH))

    def test_a_deed_with_no_kind_says_how_it_landed(self) -> None:
        src, kn = self.deed_world("", "Hurt them")
        self.assertIn("hurt by them", self.text(src, kn, PC))
        self.assertIn("hurt them", self.text(src, kn, KESH))

    def test_a_life_and_death_deed_is_kept_over_a_plain_knows_between_the_same_two(self) -> None:
        src, kn = self.deed_world("kill")
        src["links"].append(
            {"id": "l3", "a": PC, "b": KESH, "predicate": "knows", "detail": "",
             "directional": False, "secret": False, "at": 10, "ended": False, "plan": False,
             "via": None}
        )  # fmt: skip
        kn["connections"]["l3"] = [PC, KESH]
        text = self.text(src, kn, PC)
        self.assertIn("killed by them", text)  # (a stronger fact drops the bare "knows")
        self.assertNotIn("knows", text)

    def test_a_player_never_sees_a_deed(self) -> None:
        src, kn = self.deed_world("rescue")
        src["isDm"] = False
        got = run.summary_for(src, kn, RANKS, KESH, clicker=gate.Viewer.player([PC]), peek=True)
        self.assertTrue(
            got is None or ("rescued" not in got.text and "Saved the mill" not in got.text)
        )

    def test_a_deed_with_three_viewers_gives_three_facts(self) -> None:
        src, kn = self.deed_world("save")
        src["links"].append(
            {"id": "l3", "a": VEIL, "b": DORN, "predicate": "affected", "detail": "Helped them",
             "directional": False, "secret": True, "at": 10, "ended": False, "plan": False,
             "via": None}
        )  # fmt: skip
        kn["connections"]["l3"] = [VEIL, DORN]
        text = self.text(src, kn, PC)
        self.assertIn("Kesh", text)
        self.assertIn("Dorn", text)

    def test_a_deed_nobody_was_affected_by_shows_nothing(self) -> None:
        src, kn = self.deed_world("rescue")
        src["links"] = [link for link in src["links"] if link["id"] == "l1"]
        self.assertNotIn("rescued", self.text(src, kn, PC))

    def test_an_unknown_effect_falls_back_to_the_kind_and_an_unknown_kind_to_the_effect(
        self,
    ) -> None:
        src, kn = self.deed_world("rescue", "Loved them")
        self.assertIn("rescued by them", self.text(src, kn, PC))
        src, kn = self.deed_world("murder", "Hurt them")
        self.assertIn("hurt by them", self.text(src, kn, PC))

    def test_a_deed_two_characters_did_gives_each_a_fact(self) -> None:
        src, kn = self.deed_world("save")
        src["links"].append(
            {"id": "l4", "a": DORN, "b": VEIL, "predicate": "did", "detail": "save",
             "directional": False, "secret": True, "at": 10, "ended": False, "plan": False,
             "via": None}
        )  # fmt: skip
        kn["connections"]["l4"] = [DORN, VEIL]
        self.assertIn("Kesh", self.text(src, kn, PC))
        self.assertIn("Kesh", self.text(src, kn, DORN))
