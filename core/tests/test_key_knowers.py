"""Who knows each found row, scored against the key (#4523 slice 2, as Supervisor ruled): a player
character who knows what the key keeps from them is a leak (a failure), one who should know and
doesn't is too narrow, and an NPC the key names is judged on its own line. The gate's own rule says
who knows (a sleeping row grants nothing, a group's row reaches its members, a known connection
makes its ends known). Pure: small invented backups; the built keys are read for their shape."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from dmbot.devtools import build_keys, key_knowers
from dmbot.devtools import graph_key_check as gkc
from tests.test_graph_key_check import rel

PCS = ("Mira", "Tavin")  # (invented player characters)


def entity(i: str, name: str, *, pc: bool = False, kind: str = "character") -> dict[str, Any]:
    return {
        "table": "entity", "id": i, "name": name, "type": kind, "status": "confirmed",
        "merged_into": None, "role": "player_character" if pc else None,
    }  # fmt: skip


def knower(
    who: str, *, relation: str = "", entry: str = "", asleep: bool = False
) -> dict[str, Any]:
    return {
        "table": "knower", "id": f"k-{who}-{relation or entry}", "perspective_id": f"p-{who}",
        "about_entity_id": entry or None, "state_id": None, "relation_id": relation or None,
        "alias_id": None, "confidence": 1.0, "hidden_by": ["x"] if asleep else [],
    }  # fmt: skip


def backup(knowers: list[dict[str, Any]], *more: dict[str, Any]) -> dict[str, Any]:
    people = [("mira", "Mira", True), ("tavin", "Tavin", True), ("ulf", "Ulf", False)]
    rows: list[dict[str, Any]] = [entity(i, n, pc=pc) for i, n, pc in people]
    rows += [
        entity("hall", "Stonehall", kind="place"),
        entity("guild", "Lamplighters", kind="faction"),
    ]
    rows += [
        {"table": "perspective", "id": f"p-{i}", "kind": "pc" if pc else "npc", "entity_id": i}
        for i, _, pc in people
    ]
    rows += [rel("r1", "mira", "kin_of", "ulf", "sibling"), *more, *knowers]
    return {"campaign": {"name": gkc.SAMPLE_CAMPAIGN}, "sections": {"memory": rows}}


def key(tie_knowers: Any, hall_knowers: Any = "party") -> dict[str, Any]:
    return {
        "players": {p: "player" for p in PCS},
        "nodes": [
            {"name": "Mira", "kind": "person", "aliases": ["Norro"], "knowers": "party"},
            {"name": "Tavin", "kind": "person", "knowers": "party"},
            {"name": "Ulf", "kind": "person", "knowers": ["Mira", "Ulf"]},
            {"name": "Stonehall", "kind": "place", "knowers": hall_knowers},
        ],
        "pairs": [
            {"id": "t1", "a": "Mira", "relation": "sibling", "b": "Ulf", "tier": "stated",
             "must_find": True, "knowers": tie_knowers},
        ],
    }  # fmt: skip


def scored(report: gkc.Report) -> dict[str, key_knowers.Scored]:
    return {s.id: s for s in report.knowers}


class TheVerdicts(unittest.TestCase):
    def test_the_two_who_lived_it_know_it_right(self) -> None:
        both = [knower("mira", relation="r1"), knower("ulf", relation="r1")]
        got = scored(gkc.check(backup(both), key(["Mira", "Ulf"])))
        self.assertEqual(
            (got["t1"].verdict, got["t1"].npc), (key_knowers.RIGHT, key_knowers.NPC_RIGHT)
        )

    def test_a_player_character_who_knows_a_secret_is_a_leak(self) -> None:
        rows = [knower(w, relation="r1") for w in ("mira", "ulf", "tavin")]
        got = scored(gkc.check(backup(rows), key(["Mira", "Ulf"])))
        self.assertEqual(got["t1"].verdict, key_knowers.LEAKED)

    def test_the_party_short_of_one_is_too_narrow(self) -> None:
        got = scored(gkc.check(backup([knower("mira", relation="r1")]), key("party")))
        self.assertEqual(got["t1"].verdict, key_knowers.NARROW)
        self.assertEqual(got["t1"].npc, "")  # (the key names no NPC: none is judged)

    def test_a_row_asleep_under_a_hide_grants_nothing(self) -> None:
        rows = [knower("mira", relation="r1"), knower("tavin", relation="r1", asleep=True)]
        got = scored(gkc.check(backup(rows), key(["Mira"])))
        self.assertEqual(got["t1"].verdict, key_knowers.RIGHT)

    def test_the_dms_alone_with_an_npc_knowing_is_a_wrong_npc_not_a_failure(self) -> None:
        got = scored(gkc.check(backup([knower("ulf", relation="r1")]), key("dm")))
        self.assertEqual(
            (got["t1"].verdict, got["t1"].npc), (key_knowers.RIGHT, key_knowers.NPC_WRONG)
        )

    def test_an_entry_is_known_by_its_own_name_only(self) -> None:
        """Knowing the tie lets the gate count Ulf as known of, but the key's entry knowers say who
        knows his name (as `gate.display_name` reads it), so only an entry row of its own counts."""
        both = [knower("mira", relation="r1"), knower("ulf", relation="r1")]
        got = scored(gkc.check(backup(both), key(["Mira", "Ulf"])))
        self.assertEqual(got["Ulf"].verdict, key_knowers.NARROW)
        named = scored(gkc.check(backup([*both, knower("mira", entry="ulf")]), key(["Mira"])))
        self.assertEqual(named["Ulf"].verdict, key_knowers.RIGHT)

    def test_a_player_character_knows_their_own_entry_without_a_row(self) -> None:
        got = scored(gkc.check(backup([knower("tavin", entry="mira")]), key("party")))
        self.assertEqual(got["Mira"].verdict, key_knowers.RIGHT)  # (Tavin's row, and Mira herself)
        alone = scored(gkc.check(backup([]), key("party")))
        self.assertEqual(alone["Mira"].verdict, key_knowers.NARROW)  # (Tavin has no row)

    def test_everyone_scores_as_the_party(self) -> None:
        rows = [knower(w, relation="r1") for w in ("mira", "tavin")]
        for word in ("party", "everyone"):
            with self.subTest(word):
                got = scored(gkc.check(backup(rows), key(word)))
                self.assertEqual(got["t1"].verdict, key_knowers.RIGHT)

    def test_a_word_the_key_does_not_know_stops_the_run(self) -> None:
        with self.assertRaises(ValueError):
            gkc.check(backup([knower("mira", relation="r1")]), key("Party"))

    def test_a_turned_down_tie_grants_nothing(self) -> None:
        """A second, rejected row between the two carries Tavin's knowing: it is not a leak."""
        dead = rel("r2", "mira", "kin_of", "ulf", "sibling", status="rejected")
        rows = [knower("mira", relation="r1"), knower("tavin", relation="r2")]
        got = scored(gkc.check(backup(rows, dead), key(["Mira"])))
        self.assertEqual(got["t1"].verdict, key_knowers.RIGHT)

    def test_a_waiting_suggestion_is_not_scored(self) -> None:
        data = backup([knower("tavin", relation="r1")])
        for row in data["sections"]["memory"]:
            if row.get("id") == "r1":
                row["status"] = "proposed"
        self.assertNotIn("t1", scored(gkc.check(data, key(["Mira"]))))

    def test_another_entry_with_a_characters_other_name_lends_nothing(self) -> None:
        """A place stored as "Norro" (Mira's other name in the key) knows nothing for her."""
        rows = [knower("norro", relation="r1"), knower("mira", relation="r1")]
        data = backup(rows, entity("norro", "Norro", kind="place"))
        data["sections"]["memory"].append(
            {"table": "perspective", "id": "p-norro", "kind": "npc", "entity_id": "norro"}
        )
        got = scored(gkc.check(data, key(["Mira"])))
        self.assertEqual(got["t1"].verdict, key_knowers.RIGHT)

    def test_a_groups_row_reaches_its_members(self) -> None:
        member = [rel(f"m-{p}", p, "member_of", "guild") for p in ("mira", "tavin")]
        rows = [{**knower("guild", entry="hall"), "perspective_id": "p-guild"}]
        data = backup(rows, *member)
        data["sections"]["memory"].append(
            {"table": "perspective", "id": "p-guild", "kind": "npc", "entity_id": "guild"}
        )
        got = scored(gkc.check(data, key("party")))
        self.assertEqual(got["Stonehall"].verdict, key_knowers.RIGHT)

    def test_the_partys_own_row_and_a_waiting_membership_reach_nobody(self) -> None:
        member = [rel("m-mira", "mira", "member_of", "guild"),
                  rel("m-tavin", "tavin", "member_of", "guild", status="proposed")]  # fmt: skip
        rows = [{**knower("guild", entry="hall"), "perspective_id": "p-guild"}]
        for party in (True, False):
            with self.subTest(party=party):
                data = backup(rows, *member)
                data["sections"]["memory"].append(
                    {"table": "perspective", "id": "p-guild", "kind": "npc", "entity_id": "guild"}
                )
                for row in data["sections"]["memory"]:
                    if row.get("id") == "guild":
                        row["party"] = party
                got = scored(gkc.check(data, key("party")))
                self.assertEqual(got["Stonehall"].verdict, key_knowers.NARROW)

    def test_rows_the_owner_is_checking_are_counted_apart(self) -> None:
        checking = key(["Mira"])
        checking["pairs"][0]["check"] = True
        rows = [knower(w, relation="r1") for w in ("mira", "tavin")]
        counts = gkc.summarize(gkc.check(backup(rows), checking))["knowers"]
        self.assertEqual(counts["checked"], {key_knowers.LEAKED: 1})
        self.assertEqual(counts["ties"], {key_knowers.LEAKED: 1})

    def test_a_key_without_knowers_scores_none(self) -> None:
        plain = key("party")
        for section in ("nodes", "pairs"):
            for row in plain[section]:
                del row["knowers"]
        report = gkc.check(backup([knower("mira", relation="r1")]), plain)
        self.assertEqual(report.knowers, [])
        self.assertNotIn("knowers", gkc.summarize(report))

    def test_the_report_lists_a_leak_by_its_id(self) -> None:
        rows = [knower(w, relation="r1") for w in ("mira", "ulf", "tavin")]
        text = gkc.render(gkc.check(backup(rows), key(["Mira", "Ulf"])), counts_only=True)
        self.assertIn("Leaks (a player character knows what the key keeps from them): 1", text)
        self.assertIn("  tie t1", text)
        self.assertNotIn("Stonehall", text)  # (counts only: an entry's name is never printed)


class TheBuiltKeys(unittest.TestCase):
    """The scorer reads the built keys whole: every row of every document it is asked for."""

    def load(self, name: str) -> dict[str, Any]:
        return dict(json.loads((build_keys.KEYS / name).read_text(encoding="utf-8")))

    def test_all_means_every_document_the_key_holds(self) -> None:
        total = self.load("total_key.json")
        stories = {f"backstory_{s}" for s in build_keys.BACKSTORIES}
        self.assertEqual(gkc.docs_wanted(total, "all"), {"lore"} | stories)
        self.assertEqual(gkc.docs_wanted(total, gkc.BACKSTORIES), stories)
        self.assertIn("session", gkc.docs_wanted(self.load("full_key.json"), "all"))

    def test_every_row_of_a_built_key_is_scored(self) -> None:
        empty = {"campaign": {"name": gkc.SAMPLE_CAMPAIGN}, "sections": {"memory": []}}
        for name in build_keys.BUILT:
            the_key = self.load(name)
            with self.subTest(name):
                report = gkc.check(empty, the_key, "all")
                self.assertEqual(len(report.ties), len(the_key["pairs"]))
                self.assertEqual(len(report.entries), len(the_key["nodes"]))
                self.assertEqual(report.knowers, [])  # (nothing found, so nobody is judged)

    def test_the_command_takes_the_new_choices(self) -> None:
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "b.json"
            file.write_text(json.dumps(backup([knower("mira", relation="r1")])))
            the_key = str(build_keys.KEYS / "total_key.json")
            with redirect_stdout(out):
                code = gkc.main([str(file), "--key", the_key, "--docs", "all", "--counts-only"])
        self.assertEqual(code, 0)
        self.assertIn("Documents scored: all", out.getvalue())
        self.assertIn("Who knows found ties (player characters):", out.getvalue())
        for node in self.load("total_key.json")["nodes"]:
            if node["kind"] == "person":  # (counts only: no entry's name)
                self.assertNotIn(f"entry {node['name']}", out.getvalue())

    def test_backstories_on_a_key_without_them_is_refused(self) -> None:
        master = (
            self.load("master_key.json") if (build_keys.KEYS / "master_key.json").exists() else {}
        )
        empty = {"campaign": {"name": gkc.SAMPLE_CAMPAIGN}, "sections": {"memory": []}}
        with self.assertRaises(ValueError):
            gkc.check(empty, master, gkc.BACKSTORIES)


if __name__ == "__main__":
    unittest.main()
