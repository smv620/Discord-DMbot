"""The story graph answer keys and the check that scores a backup against them (#3244). Pure: a
small made-up backup stands in for the campaign, and the real key files are read for their shape."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from dmbot.devtools import graph_key_check as gkc

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"
TIERS = {"stated", "needs_context", "inferred", "recap_only", "by_rule"}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


class TheKeys(unittest.TestCase):
    def test_every_key_is_well_formed(self) -> None:
        for name in ("lore_key.json", "session_key.json", "master_key.json"):
            key = load(name)
            nodes = {n["name"] for n in key["nodes"]}
            ids = [p["id"] for p in key["pairs"]]
            self.assertEqual(len(ids), len(set(ids)), name)
            for p in key["pairs"]:
                self.assertIn(p["a"], nodes, (name, p["id"]))
                self.assertIn(p["b"], nodes, (name, p["id"]))
                self.assertIn(p["tier"], TIERS, (name, p["id"]))
                self.assertIn(p["relation"], gkc.GROUPS, (name, p["id"]))

    def test_the_master_is_the_two_keys_together(self) -> None:
        lore, session, master = (
            load("lore_key.json"),
            load("session_key.json"),
            load("master_key.json"),
        )
        said = {(s["doc"], s["id"]) for p in master["pairs"] for s in p["sources"]}
        self.assertEqual(
            said,
            {("lore", p["id"]) for p in lore["pairs"]}
            | {("session", p["id"]) for p in session["pairs"]},
        )
        both = [p for p in master["pairs"] if len(p["in"]) == 2]
        self.assertGreaterEqual(len(both), 10)  # the ties the session repeats from the lore
        names = {n["name"] for n in master["nodes"]}
        self.assertTrue(
            {n["name"] for n in lore["nodes"]} | {n["name"] for n in session["nodes"]} == names
        )

    def test_the_session_is_the_later_document_for_what_is_current(self) -> None:
        master = load("master_key.json")
        party_places = {
            p["b"]: p.get("current", True)
            for p in master["pairs"]
            if p["a"] == "The party" and p["relation"] == "last_seen_in"
        }
        self.assertEqual(party_places["The windbreak shelter"], True)
        self.assertEqual(party_places["Targos"], False)


def backup(
    entities: list[tuple[str, str, str]],
    relations: list[dict[str, Any]],
    aliases: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        {
            "table": "entity",
            "id": i,
            "name": n,
            "type": t,
            "status": "confirmed",
            "merged_into": None,
        }
        for i, n, t in entities
    ]
    rows += [
        {
            "table": "alias",
            "id": f"al{k}",
            "entity_id": i,
            "text": t,
            "key": t.casefold(),
            "status": "confirmed",
        }
        for k, (i, t) in enumerate(aliases)
    ]
    rows += relations
    return {"campaign": {"name": gkc.SAMPLE_CAMPAIGN}, "sections": {"memory": rows}}


def rel(
    rid: str,
    s: str,
    p: str,
    o: str,
    detail: str = "",
    status: str = "confirmed",
    ended: int | None = None,
) -> dict[str, Any]:
    return {
        "table": "relation", "id": rid, "subject_id": s, "predicate": p, "object_id": o,
        "detail": detail, "status": status, "to_session_at": ended,
    }  # fmt: skip


KEY = {
    "nodes": [
        {"name": "Cerric", "kind": "person", "aliases": ["Norro"], "note": "", "docs": ["lore"]},
        {"name": "Valyra", "kind": "person", "aliases": [], "note": "", "docs": ["lore"]},
        {"name": "Glyph", "kind": "creature", "aliases": [], "note": "", "docs": ["lore"]},
        {"name": "Aldmoor", "kind": "place", "aliases": [], "note": "", "docs": ["lore"]},
        {"name": "Moonblade", "kind": "item", "aliases": [], "note": "", "docs": ["lore"]},
        {"name": "Vault", "kind": "place", "aliases": [], "note": "", "docs": ["lore"]},
    ],
    "pairs": [
        {
            "id": "m1",
            "a": "Cerric",
            "relation": "sibling",
            "b": "Valyra",
            "tier": "stated",
            "must_find": True,
            "in": ["lore"],
        },
        {
            "id": "m2",
            "a": "Cerric",
            "relation": "owns",
            "b": "Glyph",
            "tier": "stated",
            "must_find": True,
            "in": ["lore"],
        },
        {
            "id": "m3",
            "a": "Cerric",
            "relation": "lives_in",
            "b": "Aldmoor",
            "tier": "stated",
            "must_find": True,
            "in": ["lore"],
        },
        {
            "id": "m4",
            "a": "Moonblade",
            "relation": "kept_in",
            "b": "Vault",
            "tier": "stated",
            "must_find": True,
            "in": ["lore"],
        },
        {
            "id": "m5",
            "a": "Valyra",
            "relation": "last_seen_in",
            "b": "Aldmoor",
            "tier": "inferred",
            "must_find": True,
            "in": ["lore"],
        },
        {
            "id": "m6",
            "a": "Glyph",
            "relation": "knows",
            "b": "Cerric",
            "tier": "stated",
            "must_find": False,
            "feeling": "friendly",
            "in": ["lore"],
        },
    ],
    "must_not_names": ["Dog sleds"],
}


class Scoring(unittest.TestCase):
    def entities(self) -> list[tuple[str, str, str]]:
        return [
            ("c", "Cerric", "character"), ("v", "Valyra", "character"), ("g", "Glyph", "character"),
            ("a", "Aldmoor", "place"), ("m", "Moonblade", "item"), ("t", "Vault", "place"),
        ]  # fmt: skip

    def status(self, report: gkc.Report) -> dict[str, str]:
        return {t.id: t.status for t in report.ties}

    def test_what_is_there_is_found_and_what_is_not_is_missing(self) -> None:
        data = backup(
            self.entities(), [rel("1", "c", "kin_of", "v", "sibling"), rel("2", "c", "owns", "g")]
        )
        got = self.status(gkc.check(data, KEY))
        self.assertEqual((got["m1"], got["m2"], got["m3"]), (gkc.FOUND, gkc.FOUND, gkc.MISSING))

    def test_a_family_tie_without_its_role_is_the_wrong_type(self) -> None:
        data = backup(self.entities(), [rel("1", "c", "kin_of", "v", "")])
        report = gkc.check(data, KEY)
        (m1,) = [t for t in report.ties if t.id == "m1"]
        self.assertEqual((m1.status, m1.detail), (gkc.WRONG, "family without the role"))

    def test_a_place_tie_is_read_by_its_word(self) -> None:
        for detail, expected in [("lives in", gkc.FOUND), ("works in", gkc.WRONG), ("", gkc.WRONG)]:
            data = backup(self.entities(), [rel("1", "c", "located_in", "a", detail)])
            self.assertEqual(self.status(gkc.check(data, KEY))["m3"], expected, detail)
        # no word is a sighting for something that moves; an item is only ever "kept in"
        data = backup(
            self.entities(),
            [
                rel("1", "v", "located_in", "a", ""),
                rel("2", "m", "located_in", "t", "is kept in"),
            ],
        )
        got = self.status(gkc.check(data, KEY))
        self.assertEqual((got["m5"], got["m4"]), (gkc.FOUND, gkc.FOUND))
        for detail in ("", "lives in", "was last seen in"):
            data = backup(self.entities(), [rel("2", "m", "located_in", "t", detail)])
            self.assertEqual(self.status(gkc.check(data, KEY))["m4"], gkc.WRONG, detail)

    def test_a_grandparent_is_not_a_parent(self) -> None:
        key = {
            "nodes": [
                {"name": "A", "kind": "person", "aliases": []},
                {"name": "B", "kind": "person", "aliases": []},
            ],
            "pairs": [
                {"id": "k1", "a": "A", "relation": "parent_of", "b": "B", "tier": "stated",
                 "must_find": True},
            ],
        }  # fmt: skip
        ents = [("a", "A", "character"), ("b", "B", "character")]
        for detail, expected in [
            ("grandparent", gkc.WRONG), ("parent", gkc.FOUND), ("mother", gkc.FOUND),
            ("grandmother", gkc.WRONG),
        ]:  # fmt: skip
            data = backup(ents, [rel("1", "a", "kin_of", "b", detail)])
            self.assertEqual(self.status(gkc.check(data, key))["k1"], expected, detail)

    def test_wary_is_not_a_plain_knows(self) -> None:
        key = {
            "nodes": [
                {"name": "A", "kind": "person", "aliases": []},
                {"name": "B", "kind": "person", "aliases": []},
            ],
            "pairs": [
                {"id": "k1", "a": "A", "relation": "knows", "feeling": "wary", "b": "B",
                 "tier": "stated", "must_find": True},
            ],
        }  # fmt: skip
        ents = [("a", "A", "character"), ("b", "B", "character")]
        for detail, expected in [("", gkc.WRONG), ("neutral", gkc.WRONG), ("wary", gkc.FOUND)]:
            data = backup(ents, [rel("1", "a", "knows", "b", detail)])
            self.assertEqual(self.status(gkc.check(data, key))["k1"], expected, detail)

    def test_a_past_key_tie_needs_an_ended_or_past_row_and_a_present_one_does_not_accept_it(
        self,
    ) -> None:
        key = {
            "nodes": [
                {"name": "A", "kind": "person", "aliases": []},
                {"name": "P", "kind": "place", "aliases": []},
            ],
            "pairs": [
                {"id": "now", "a": "A", "relation": "lives_in", "b": "P", "tier": "stated",
                 "must_find": True},
                {"id": "then", "a": "A", "relation": "lived_in", "b": "P", "tier": "stated",
                 "must_find": True, "current": False},
            ],
        }  # fmt: skip
        ents = [("a", "A", "character"), ("p", "P", "place")]
        live = backup(ents, [rel("1", "a", "located_in", "p", "lives in")])
        got = self.status(gkc.check(live, key))
        self.assertEqual((got["now"], got["then"]), (gkc.FOUND, gkc.WRONG))
        ended = backup(
            ents, [{**rel("1", "a", "located_in", "p", "lives in"), "to_session_at": "x"}]
        )
        got = self.status(gkc.check(ended, key))
        self.assertEqual((got["now"], got["then"]), (gkc.WRONG, gkc.FOUND))

    def test_the_command_refuses_another_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = backup(self.entities(), [])
            data["campaign"] = {"name": "someone else's table"}
            path = Path(tmp) / "b.json"
            path.write_text(json.dumps(data))
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(gkc.main([str(path)]), 2)
            self.assertNotIn("someone", err.getvalue())

    def test_the_command_reports_a_bad_file_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text("not a backup")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(gkc.main([str(path)]), 2)

    def test_a_waiting_suggestion_is_not_a_fact(self) -> None:
        data = backup(self.entities(), [rel("1", "c", "owns", "g", status="proposed")])
        self.assertEqual(self.status(gkc.check(data, KEY))["m2"], gkc.WAITING)

    def test_a_feeling_is_read_and_the_other_way_round_is_not_the_same_tie(self) -> None:
        good = backup(self.entities(), [rel("1", "g", "knows", "c", "friendly")])
        bad = backup(self.entities(), [rel("1", "g", "knows", "c", "hostile")])
        flipped = backup(self.entities(), [rel("1", "c", "knows", "g", "friendly")])
        self.assertEqual(self.status(gkc.check(good, KEY))["m6"], gkc.FOUND)
        self.assertEqual(self.status(gkc.check(bad, KEY))["m6"], gkc.WRONG)
        self.assertEqual(self.status(gkc.check(flipped, KEY))["m6"], gkc.WRONG)

    def test_a_name_is_found_by_another_name_and_extras_and_forbidden_names_are_counted(
        self,
    ) -> None:
        ents = [
            ("c", "Norro", "character"),
            ("x", "Dog sleds", "item"),
            ("y", "Stranger", "character"),
        ]
        data = backup(ents, [rel("1", "c", "knows", "y")], aliases=(("c", "Cerric"),))
        report = gkc.check(data, KEY)
        found = {e.id: e.status for e in report.entries}
        self.assertEqual((found["Cerric"], found["Valyra"]), (gkc.FOUND, gkc.MISSING))
        self.assertEqual(report.violations, ["Dog sleds"])
        self.assertEqual(sum(report.extra_entries.values()), 2)

    def test_a_wrong_kind_of_entry_is_counted(self) -> None:
        data = backup([("p", "Glyph", "place")], [])
        report = gkc.check(data, KEY)
        self.assertEqual({e.id: e.status for e in report.entries}["Glyph"], gkc.WRONG)

    def test_a_document_filter_scores_only_what_that_document_says(self) -> None:
        report = gkc.check(backup([], []), KEY, docs="session")
        self.assertEqual((report.entries, report.ties), ([], []))

    def test_counts_only_prints_no_names(self) -> None:
        data = backup(self.entities(), [])
        text = gkc.render(gkc.check(data, KEY), counts_only=True)
        self.assertIn("m1", text)
        self.assertNotIn("Valyra", text)
        self.assertIn("Valyra", gkc.render(gkc.check(data, KEY)))

    def test_the_command_reads_a_backup_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text(json.dumps(backup(self.entities(), [rel("1", "c", "owns", "g")])))
            key = Path(tmp) / "key.json"
            key.write_text(json.dumps(KEY))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(gkc.main([str(path), "--key", str(key), "--counts-only"]), 0)
            self.assertIn("Story graph check", out.getvalue())

    def test_a_file_that_is_not_a_backup_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            gkc.check({"sections": {}}, KEY)


class ThePlayerCharacters(unittest.TestCase):
    """#3553: the check says how many of the key's player characters the campaign has as such."""

    def key(self) -> dict[str, Any]:
        return load("lore_key.json")

    def entries(self, roles: dict[str, str | None]) -> dict[str, Any]:
        data = backup([(f"e{i}", n, "character") for i, n in enumerate(roles)], [])
        for row, role in zip(data["sections"]["memory"], roles.values(), strict=True):
            row["role"] = role
        return data

    def test_it_counts_the_four_that_are_player_characters(self) -> None:
        names = list(self.key()["players"])
        data = self.entries({n: "player_character" for n in names})
        report = gkc.check(data, self.key(), "lore")
        self.assertEqual((report.players_found, report.players_expected), (4, 4))
        self.assertIn("Player characters found: 4 of 4", gkc.render(report))

    def test_an_npc_with_a_players_name_does_not_count(self) -> None:
        names = list(self.key()["players"])
        roles: dict[str, str | None] = {n: "player_character" for n in names[:2]}
        roles.update({n: "npc" for n in names[2:]})
        report = gkc.check(self.entries(roles), self.key(), "lore")
        self.assertEqual(report.players_found, 2)
        self.assertIn("Player characters found: 2 of 4", gkc.render(report, counts_only=True))


class TheFeelingScore(unittest.TestCase):
    """#3606: each key feeling is scored exact, one step off, over-read or under-read."""

    def test_the_scale(self) -> None:
        from dmbot.memory import reputation_words as w

        self.assertEqual(w.compare("wary", "wary"), w.EXACT)
        self.assertEqual(w.compare("wary", "distrusts"), w.ONE_STEP)
        self.assertEqual(w.compare("wary", "hostile"), w.OVER_READ)  # the Belleros and Brewner case
        self.assertEqual(w.compare("hostile", "wary"), w.UNDER_READ)
        self.assertEqual(w.compare("friendly", "devoted"), w.OVER_READ)
        self.assertEqual(w.compare("-", "hostile"), w.EXACT)
        self.assertEqual(w.compare("+", "hostile"), w.WRONG_SIDE)
        self.assertEqual(w.compare("wary", "adores"), w.UNDER_READ)
        self.assertEqual(w.compare("wary", "neutral"), w.ONE_STEP)  # (neutral stores nothing)
        self.assertEqual(w.compare("neutral", "wary"), w.ONE_STEP)
        self.assertEqual(w.compare("wary", "trusts"), w.OVER_READ)  # (a real 0 against +40)
        self.assertEqual(w.compare("wary", "devoted"), w.OVER_READ)
        self.assertEqual(w.compare("distrusts", "devoted"), w.WRONG_SIDE)
        self.assertEqual(w.compare("hostile", "trusts"), w.WRONG_SIDE)
        self.assertEqual(w.compare("friendly", "trusts"), w.ONE_STEP)
        self.assertEqual(w.compare("devoted", "close"), w.ONE_STEP)
        self.assertEqual(w.compare(" + ", "friendly"), w.EXACT)  # (a sign is trimmed)
        self.assertEqual(w.compare("0", "wary"), w.EXACT)
        self.assertEqual(w.compare("+", "distrusts"), w.WRONG_SIDE)

    def test_a_backup_scores_the_stored_word_against_the_key(self) -> None:
        key = {
            "nodes": [
                {"name": "Belleros", "kind": "person"},
                {"name": "Brewner", "kind": "person"},
            ],
            "pairs": [
                {
                    "id": "m081", "a": "Belleros", "relation": "knows", "b": "Brewner",
                    "feeling": "wary", "tier": "stated", "must_find": False, "in": ["lore"],
                }
            ],
        }  # fmt: skip
        rel = {
            "table": "relation", "id": "r1", "subject_id": "e1", "predicate": "knows",
            "object_id": "e2", "detail": "hostile", "status": "confirmed", "secret": False,
        }  # fmt: skip
        data = backup([("e1", "Belleros", "character"), ("e2", "Brewner", "character")], [rel])
        report = gkc.check(data, key, "lore")
        self.assertEqual(report.feelings, [("m081", "over_read")])
        self.assertIn("over-read 1", gkc.render(report, counts_only=True))

    def feelings(self, key_feeling: str, rows: list[tuple[str, str, str, str]], **tie: Any) -> Any:
        key = {
            "nodes": [{"name": "A", "kind": "person"}, {"name": "B", "kind": "person"}],
            "pairs": [
                {
                    "id": "t1", "a": "A", "relation": tie.pop("relation", "knows"), "b": "B",
                    "feeling": key_feeling, "tier": tie.pop("tier", "stated"),
                    "must_find": False, "in": ["lore"],
                }
            ],
        }  # fmt: skip
        relations = [
            {
                "table": "relation", "id": f"r{n}", "subject_id": s, "predicate": p,
                "object_id": o, "detail": d, "status": "confirmed", "secret": False,
            }
            for n, (s, o, p, d) in enumerate(rows)
        ]  # fmt: skip
        data = backup([("e1", "A", "character"), ("e2", "B", "character")], relations)
        return gkc.check(data, key, "lore").feelings

    def test_the_closest_of_several_rows_is_scored_in_any_order(self) -> None:
        over = ("e1", "e2", "knows", "hostile")
        exact = ("e1", "e2", "knows", "wary")
        near = ("e1", "e2", "knows", "distrusts")
        self.assertEqual(self.feelings("wary", [over, exact]), [("t1", "exact")])
        self.assertEqual(self.feelings("wary", [exact, over]), [("t1", "exact")])
        self.assertEqual(self.feelings("wary", [over, near]), [("t1", "one_step_off")])

    def test_a_knows_with_no_word_is_neutral_and_a_garbled_detail_is_no_feeling_row(self) -> None:
        self.assertEqual(
            self.feelings("wary", [("e1", "e2", "knows", "")]), [("t1", "one_step_off")]
        )
        self.assertEqual(self.feelings("wary", [("e1", "e2", "knows", "his cousin")]), [])

    def test_the_wrong_end_of_a_knows_is_not_scored_but_an_enemy_of_reads_both_ways(self) -> None:
        self.assertEqual(self.feelings("wary", [("e2", "e1", "knows", "wary")]), [])
        got = self.feelings("hostile", [("e2", "e1", "enemy_of", "")], relation="enemy_of")
        self.assertEqual(got, [("t1", "exact")])

    def test_by_rule_ties_and_missing_ties_are_not_scored(self) -> None:
        row = ("e1", "e2", "knows", "wary")
        self.assertEqual(self.feelings("wary", [row], tier="by_rule"), [])
        self.assertEqual(self.feelings("wary", []), [])

    def test_the_render_names_the_over_and_under_reads_by_id(self) -> None:
        key = {
            "nodes": [{"name": "A", "kind": "person"}, {"name": "B", "kind": "person"}],
            "pairs": [
                {
                    "id": "t1", "a": "A", "relation": "knows", "b": "B", "feeling": "wary",
                    "tier": "stated", "must_find": False, "in": ["lore"],
                }
            ],
        }  # fmt: skip
        rel = {
            "table": "relation", "id": "r1", "subject_id": "e1", "predicate": "knows",
            "object_id": "e2", "detail": "trusts", "status": "confirmed", "secret": False,
        }  # fmt: skip
        data = backup([("e1", "A", "character"), ("e2", "B", "character")], [rel])
        text = gkc.render(gkc.check(data, key, "lore"), counts_only=True)
        self.assertIn("  t1  over_read", text)


class ByDesign(unittest.TestCase):
    """#3545: a kind of creature the rules hold is left out on purpose, so it is not a miss."""

    def key(self) -> dict[str, Any]:
        return load("lore_key.json")

    def test_the_three_kinds_are_marked(self) -> None:
        marked = {n["name"] for n in self.key()["nodes"] if n.get("by_design")}
        self.assertEqual(marked, {"Yeti", "Ice troll", "Mind flayer"})

    def test_absent_they_are_listed_apart_and_never_missed(self) -> None:
        report = gkc.check(backup([], []), self.key(), "lore")
        by_design = {e.id for e in report.entries if e.status == gkc.BY_DESIGN}
        self.assertEqual(by_design, {"Yeti", "Ice troll", "Mind flayer"})
        self.assertFalse(any(e.must for e in report.entries if e.status == gkc.BY_DESIGN))
        creatures = gkc.summarize(report)["entries"]["creature"]
        self.assertEqual(creatures[gkc.BY_DESIGN], 3)
        self.assertEqual(creatures["must_missed"], creatures["total"] - 3)  # (the rest are misses)
        text = gkc.render(report, counts_only=True)
        self.assertIn("Entries left out by design (kinds the rules hold, not misses): 3", text)

    def test_found_they_count_as_found(self) -> None:
        report = gkc.check(backup([("e1", "Yeti", "character")], []), self.key(), "lore")
        row = next(e for e in report.entries if e.id == "Yeti")
        self.assertEqual(row.status, gkc.FOUND)


if __name__ == "__main__":
    unittest.main()
