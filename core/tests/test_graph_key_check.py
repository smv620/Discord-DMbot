"""The story graph answer keys and the check that scores a backup against them (#3244). Pure: a
small made-up backup stands in for the campaign, and the real key files are read for their shape."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, ClassVar

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


class ThePlaceholders(unittest.TestCase):
    """#3545: a `Player 1234` stand-in nothing uses is not an extra entry; one with a tie is."""

    def key(self) -> dict[str, Any]:
        return load("lore_key.json")

    def test_an_unused_placeholder_is_left_out_and_counted(self) -> None:
        data = backup([("p1", "Player 4821", "person"), ("p2", "Player 1177", "person")], [])
        report = gkc.check(data, self.key(), "lore")
        self.assertEqual(report.placeholders, 2)
        self.assertEqual(sum(report.extra_entries.values()), 0)
        self.assertIn("Player placeholders left out (not extras): 2", gkc.render(report))

    def test_a_placeholder_that_only_plays_a_character_is_left_out(self) -> None:
        plays = {
            "table": "relation", "id": "r1", "subject_id": "p1", "predicate": "plays",
            "object_id": "x1", "detail": "", "status": "confirmed", "secret": False,
        }  # fmt: skip
        data = backup(
            [("p1", "Player 4821", "person"), ("x1", "Cerric Vanya'elen", "character")], [plays]
        )
        report = gkc.check(data, self.key(), "lore")
        self.assertEqual(report.placeholders, 1)
        self.assertEqual(sum(report.extra_entries.values()), 0)

    def test_a_placeholder_with_a_connection_is_still_an_extra(self) -> None:
        rel = {
            "table": "relation", "id": "r1", "subject_id": "p1", "predicate": "knows",
            "object_id": "x1", "detail": "", "status": "confirmed", "secret": False,
        }  # fmt: skip
        data = backup([("p1", "Player 4821", "person"), ("x1", "Someone Else", "character")], [rel])
        report = gkc.check(data, self.key(), "lore")
        self.assertEqual(report.placeholders, 0)
        self.assertEqual(sum(report.extra_entries.values()), 2)


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


class UnnamedPeople(unittest.TestCase):
    """A person the story never named is found by its description (#3736), in today's form and an
    older backup's, and one the key does not list is counted on its own line."""

    def test_found_by_description_and_counted_apart(self) -> None:
        data = backup(
            [
                ("c1", "Cerric", "character"),
                ("u1", "unnamed - Cerric's sister", "character"),
                ("u2", "a hooded figure (name unknown)", "character"),
                ("u3", "unnamed - loud sailors in the corner", "character"),
            ],
            [],
        )
        for row in data["sections"]["memory"]:
            if str(row.get("id", "")).startswith("u"):
                row["unnamed"] = True
        graph = gkc.Graph(gkc._tables(data))
        self.assertEqual(graph.ids(["Cerric's sister"]), {"u1"})
        self.assertEqual(graph.ids(["The hooded figure"]), {"u2"})  # (no article either side)
        self.assertEqual(graph.ids(["Cerric"]), {"c1"})  # (a name never meets a description)
        report = gkc.check(data, KEY)
        self.assertEqual(report.unnamed_extras, 3)
        self.assertEqual(sum(report.extra_entries.values()), 0)

    def raider_key(self) -> dict[str, Any]:
        return {
            "nodes": [{"name": "The barbarian with the maul", "kind": "person",
                       "aliases": ["maul barbarian"], "unnamed": True, "must_find": False}],
            "pairs": [], "must_not_names": ["maul barbarian"],
        }  # fmt: skip

    def test_an_unnamed_key_entry_is_found_by_description_never_by_a_name(self) -> None:
        # (Supervisor's ruling on #3545: the raider is right as a person the story never names,
        # and still a miss when his description is stored as a name)
        named = backup([("n1", "maul barbarian", "character")], [])
        report = gkc.check(named, self.raider_key())
        (row,) = report.entries
        self.assertEqual(row.status, gkc.MISSING)
        self.assertFalse(row.must)  # (a good find, not a must)
        self.assertEqual(report.violations, ["maul barbarian"])
        unnamed = backup([("u1", "unnamed - maul barbarian", "character")], [])
        unnamed["sections"]["memory"][0]["unnamed"] = True
        report = gkc.check(unnamed, self.raider_key())
        (row,) = report.entries
        self.assertEqual(row.status, gkc.FOUND)
        self.assertEqual(report.violations, [])  # (a description is no must-not name)
        old = backup([("u2", "maul barbarian (name unknown)", "character")], [])
        old["sections"]["memory"][0]["unnamed"] = True  # (an older backup's form)
        report = gkc.check(old, self.raider_key())
        self.assertEqual((report.entries[0].status, report.violations), (gkc.FOUND, []))

    def test_the_wind_spirit_is_found_by_the_lores_own_words(self) -> None:
        # (the owner, 2026-11-10: lore line 30 says "an ancient wind spirit", which DMbot keeps as
        # "unnamed - ancient wind spirit"; with only "The wind spirit" in the key, a right find and
        # its tie to Okthandom, l035, both scored as misses)
        key = load("lore_key.json")
        data = backup(
            [("ok", "Okthandom Korran", "character"), ("ws", "unnamed - ancient wind spirit",
             "creature")],
            [rel("r1", "ws", "associated_with", "ok")],
        )  # fmt: skip
        data["sections"]["memory"][1]["unnamed"] = True
        report = gkc.check(data, key, "lore")
        (spirit,) = [e for e in report.entries if e.id == "The wind spirit"]
        (tie,) = [t for t in report.ties if t.id == "l035"]
        self.assertEqual((spirit.status, tie.status), (gkc.FOUND, gkc.FOUND))

    def test_extras_are_listed_by_name_only_when_asked(self) -> None:
        # (#5055: dev1 lists what each run stored that the key does not match, on #3545)
        data = backup(
            [("c1", "Cerric", "character"), ("x1", "Underdark", "place"),
             ("u1", "unnamed - Cerric's grandfather", "character")],
            [],
        )  # fmt: skip
        data["sections"]["memory"][2]["unnamed"] = True
        for row in data["sections"]["memory"]:
            row["source"] = "lore"
        report = gkc.check(data, KEY)
        listed = gkc.render(report, counts_only=True, list_extras=True)
        self.assertIn("place | confirmed | lore | Underdark", listed)
        self.assertIn("unnamed - Cerric's grandfather", listed)
        self.assertNotIn("Underdark", gkc.render(report, counts_only=True))
        self.assertNotIn("| Cerric\n", listed)  # (a key match is never an extra)

    def test_other_must_not_names_still_count_as_descriptions(self) -> None:
        key = {**self.raider_key(), "must_not_names": ["maul barbarian", "the attackers"]}
        data = backup([("u1", "unnamed - the attackers", "character")], [])
        data["sections"]["memory"][0]["unnamed"] = True
        self.assertEqual(gkc.check(data, key).violations, ["the attackers"])


if __name__ == "__main__":
    unittest.main()


class DescribedThings(unittest.TestCase):
    """A thing the story describes but never names (#5055 D, Supervisor's ruling): scored on its
    own line, found as an entry or (a being only) on the met-but-not-named list, never a name
    miss; its ties are found by the same words."""

    KEY: ClassVar[dict[str, Any]] = {
        "nodes": [
            {"name": "The kennel", "kind": "place", "aliases": [], "described": True,
             "said_as": ["kennel"]},
            {"name": "Garret's old sled", "kind": "item", "aliases": [], "described": True,
             "said_as": ["Garret's old sled", "old sled"]},
            {"name": "Garret's clients", "kind": "group", "aliases": [], "described": True,
             "said_as": ["Garret's clients"]},
            {"name": "Garret Velryn", "kind": "person", "aliases": []},
        ],
        "pairs": [
            {"id": "d1", "a": "Garret Velryn", "relation": "owns", "b": "Garret's old sled",
             "tier": "stated", "must_find": True, "in": ["session"]},
        ],
    }  # fmt: skip

    def statuses(self, report: gkc.Report) -> dict[str, str]:
        return {r.id: r.status for r in report.described}

    def test_found_as_an_entry_with_its_tie_or_a_being_on_the_met_list(self) -> None:
        data = backup(
            [("g1", "Garret Velryn", "character"), ("s1", "Old sled", "item")],
            [rel("r1", "g1", "owns", "s1")],
        )
        met = ["Garret's clients, waiting by the dock.", "the dogs in the kennel"]
        report = gkc.check(data, self.KEY, met=met)
        self.assertEqual([e.id for e in report.entries], ["Garret Velryn"])  # (not in the names)
        self.assertEqual(
            self.statuses(report),
            {
                "Garret's old sled": gkc.AS_ENTRY,
                "Garret's clients": gkc.ON_MET_LIST,  # (punctuation and all)
                "The kennel": gkc.MISSING,  # (a place is never on the met list: beings only)
            },
        )
        (tie,) = report.ties
        self.assertEqual(tie.status, gkc.FOUND)  # (the sled's tie, found by its said_as words)
        self.assertEqual((sum(report.extra_entries.values()), report.extra_ties), (0, 0))
        self.assertIn(
            "Described, never named: found 2 of 3 (1 as an entry, 1 on the met list)",
            gkc.render(report, counts_only=True),
        )
        self.assertEqual(gkc.summarize(report)["described"]["total"], 3)

    def test_a_person_the_story_never_named_is_found_by_description(self) -> None:
        data = backup([("u1", "unnamed - Garret's clients", "character")], [])
        data["sections"]["memory"][0]["unnamed"] = True
        self.assertEqual(self.statuses(gkc.check(data, self.KEY))["Garret's clients"], gkc.AS_ENTRY)

    def test_a_word_inside_another_word_is_no_find(self) -> None:
        report = gkc.check(backup([], []), self.KEY, met=["Garret's clientele"])
        self.assertIn(
            "Described, never named: found 0 of 3 (0 as an entry, 0 on the met list)",
            gkc.render(report, counts_only=True),
        )

    def test_the_command_reads_the_met_words_from_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "key.json").write_text(json.dumps(self.KEY))
            (folder / "b.json").write_text(json.dumps(backup([], [])))
            (folder / "met.json").write_text(json.dumps(["Garret's clients"]))
            (folder / "bad.json").write_text(json.dumps("Garret's clients"))
            base = [str(folder / "b.json"), "--key", str(folder / "key.json"), "--counts-only"]
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(gkc.main([*base, "--met", str(folder / "met.json")]), 0)
            self.assertIn("found 1 of 3 (0 as an entry, 1 on the met list)", out.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(gkc.main([*base, "--met", str(folder / "bad.json")]), 2)

    def test_the_sample_keys_mark_the_four_described_rows(self) -> None:
        for name in ("session_key.json", "master_key.json"):
            rows = [n["name"] for n in load(name)["nodes"] if n.get("described")]
            self.assertEqual(
                sorted(rows),
                ["Garret's clients", "Garret's old sled", "The kennel", "The windbreak shelter"],
                name,
            )


class PlaceFacts(unittest.TestCase):
    """A place's land, climate and ruler, scored per layer (#4256)."""

    def test_found_wrong_missed_and_extra(self) -> None:
        key = {
            "nodes": [
                {"name": "Dale", "kind": "place", "aliases": [], "note": ""},
                {"name": "Vault", "kind": "place", "aliases": [], "note": ""},
                {"name": "Council", "kind": "group", "aliases": [], "note": ""},
            ],
            "pairs": [],
            "map_facts": [
                {"place": "Dale", "layer": "climate", "value": "arctic"},
                {"place": "Vault", "layer": "terrain", "value": "underdark"},
                {"place": "Dale", "layer": "control", "value": "Council"},
            ],
        }

        def fact(
            i: str, place: str, layer: str, value: str = "", group: str | None = None
        ) -> dict[str, Any]:
            return {
                "table": "map_fact", "id": i, "entity_id": place, "layer": layer, "value": value,
                "faction_id": group, "status": "read", "to_session_at": None,
            }  # fmt: skip

        rows = [
            fact("f1", "d", "climate", "arctic"),  # found
            fact("f2", "v", "terrain", "mountain"),  # wrong
            fact("f3", "d", "terrain", "hill"),  # extra: the key says nothing of Dale's land
        ]
        data = backup(
            [("d", "Dale", "place"), ("v", "Vault", "place"), ("c", "Council", "faction")], rows
        )
        report = gkc.check(data, key)
        self.assertEqual(report.map_facts["climate"]["found"], 1)
        self.assertEqual(report.map_facts["terrain"]["wrong"], 1)
        self.assertEqual(report.map_facts["terrain"]["extra"], 1)
        self.assertEqual(report.map_facts["control"]["missed"], 1)
        self.assertIn("Place climate: found 1, wrong 0, missed 0, extra 0", gkc.render(report))

    def test_culture_and_population_rows_the_key_lacks_are_false_notes(self) -> None:
        """The sample keys list no culture or people (#4257 3a): any such row is an extra."""
        key = {
            "nodes": [{"name": "Dale", "kind": "place", "aliases": [], "note": ""}],
            "pairs": [],
            "map_facts": [],
        }
        rows = [
            {"table": "map_fact", "id": "f1", "entity_id": "d", "layer": "culture",
             "value": "dwarf", "faction_id": None, "status": "read", "to_session_at": None},
            {"table": "map_fact", "id": "f2", "entity_id": "d", "layer": "population",
             "value": "human", "faction_id": None, "status": "read", "to_session_at": None},
        ]  # fmt: skip
        report = gkc.check(backup([("d", "Dale", "place")], rows), key)
        self.assertEqual(report.map_facts["culture"]["extra"], 1)
        self.assertEqual(report.map_facts["population"]["extra"], 1)


class Languages(unittest.TestCase):
    """A character's languages, a property of the character and never a tie (#4229; owner,
    2026-11-14): found, missed and extra."""

    def test_found_missed_and_extra(self) -> None:
        key = {
            "nodes": [
                {"name": "Norrin", "kind": "person", "aliases": [], "note": ""},
                {"name": "Tamsin", "kind": "person", "aliases": [], "note": ""},
            ],
            "pairs": [],
            "languages": [
                {"character": "Norrin", "language": "Elvish"},
                {"character": "Norrin", "language": "Draconic"},
            ],
        }

        def lang(i: str, who: str, name: str) -> dict[str, Any]:
            return {
                "table": "story_language", "id": i, "entity_id": who, "name": name,
                "name_key": name.casefold(), "given_by": "read",
            }  # fmt: skip

        rows = [
            lang("a", "n", "elvish"),  # found (the same word, another case)
            lang("b", "t", "Sylvan"),  # extra: the key gives Tamsin no language
            lang("c", "t", "Draconic"),  # extra: Draconic is Norrin's, not Tamsin's
        ]
        data = backup([("n", "Norrin", "person"), ("t", "Tamsin", "person")], rows)
        report = gkc.check(data, key)
        self.assertEqual(dict(report.languages), {"found": 1, "missed": 1, "extra": 2})
        self.assertIn("Languages: found 1, missed 1, extra 2", gkc.render(report))
        self.assertEqual(gkc.summarize(report)["languages"]["extra"], 2)

    def test_a_key_without_languages_scores_none_even_with_rows(self) -> None:
        row = {
            "table": "story_language", "id": "a", "entity_id": "n", "name": "Elvish",
            "name_key": "elvish", "given_by": "read",
        }  # fmt: skip
        report = gkc.check(backup([("n", "Norrin", "person")], [row]), {"nodes": [], "pairs": []})
        self.assertEqual(report.languages, Counter())
        self.assertNotIn("Languages:", gkc.render(report))
        self.assertNotIn("languages", gkc.summarize(report))

    def test_a_session_only_run_never_scores_the_lores_languages(self) -> None:
        report = gkc.check(backup([], []), load("master_key.json"), docs="session")
        self.assertEqual(report.languages, Counter())
        both = gkc.check(backup([], []), load("master_key.json"), docs="both")
        self.assertEqual(both.languages["missed"], 2)

    def test_the_sample_keys_give_cerric_two_languages_and_no_tie(self) -> None:
        for name in ("lore_key.json", "master_key.json"):
            key = load(name)
            self.assertEqual(
                sorted((x["character"], x["language"]) for x in key["languages"]),
                [("Cerric Vanya'elen", "Draconic"), ("Cerric Vanya'elen", "Elvish")],
            )
            self.assertFalse([p for p in key["pairs"] if "language" in p["relation"]])


class Feelings(unittest.TestCase):
    """A place's, group's or people's feeling toward a lineage or a named culture (#4229 slice 4):
    found at any step, missed, and extra; the sample lore states none."""

    def row(self, holder: str, **toward: Any) -> dict[str, Any]:
        row: dict[str, Any] = {"table": "attitude", "id": holder + str(len(toward)),
                               "holder_id": holder, "toward_id": None, "toward_lineage": None,
                               "toward_culture_id": None, "closed_at": None}  # fmt: skip
        return {**row, **toward}

    def test_found_missed_and_extra(self) -> None:
        key = {
            "nodes": [{"name": "Mithral Keep", "kind": "place", "aliases": [], "note": ""}],
            "pairs": [],
            "attitudes": [
                {"holder": "Mithral Keep", "toward": "elf"},
                {"holder": "Mithral Keep", "toward": "Varnish"},
            ],
        }
        rows = [self.row("k", toward_lineage="elf"), self.row("k", toward_lineage="orc")]
        report = gkc.check(backup([("k", "Mithral Keep", "place")], rows), key)
        self.assertEqual(report.attitudes, Counter(found=1, missed=1, extra=1))
        self.assertIn("Feelings: found 1, missed 1, extra 1", gkc.render(report))

    def test_the_sample_lore_states_none(self) -> None:
        self.assertEqual(load("lore_key.json")["attitudes"], [])


class Roads(unittest.TestCase):
    """A road between two places, read either way round, and how far it is: a property of the road
    scored like languages (#4255; Supervisor's ruling): found, missed and extra."""

    KEY: ClassVar[dict[str, Any]] = {
        "nodes": [
            {"name": "Kelvenmoor", "kind": "place", "aliases": [], "note": ""},
            {"name": "Brindleford", "kind": "place", "aliases": [], "note": ""},
            {"name": "Ashwick", "kind": "place", "aliases": [], "note": ""},
        ],
        "pairs": [
            {"id": "r1", "a": "Kelvenmoor", "relation": "route", "b": "Brindleford",
             "tier": "stated", "must_find": False, "line": 1},
        ],
        "route_lengths": [
            {"from": "Kelvenmoor", "to": "Brindleford", "words": "1 hour", "line": 1},
            {"from": "Kelvenmoor", "to": "Ashwick", "words": "a few days", "line": 2},
        ],
    }  # fmt: skip
    PLACES: ClassVar[list[tuple[str, str, str]]] = [
        ("k", "Kelvenmoor", "place"),
        ("b", "Brindleford", "place"),
        ("a", "Ashwick", "place"),
    ]

    def road(self, rid: str, s: str, o: str, words: str = "", **length: Any) -> dict[str, Any]:
        return {**rel(rid, s, "route_to", o), "length_words": words or None, **length}

    def test_a_road_is_found_either_way_round(self) -> None:
        report = gkc.check(backup(self.PLACES, [self.road("x", "b", "k")]), self.KEY)
        self.assertEqual({t.id: t.status for t in report.ties}, {"r1": gkc.FOUND})

    def test_found_by_amount_and_unit_missed_and_extra(self) -> None:
        rows = [
            # "an hour" is "1 hour": the same amount and unit, the road read the other way round
            self.road("x", "b", "k", "an hour", length_amount=1.0, length_unit="hours"),
            self.road("y", "a", "b", "two days", length_amount=2.0, length_unit="days"),  # extra
        ]
        report = gkc.check(backup(self.PLACES, rows), self.KEY)
        self.assertEqual(dict(report.route_lengths), {"found": 1, "missed": 1, "extra": 1})
        self.assertIn("Road lengths: found 1, missed 1, extra 1", gkc.render(report))
        self.assertEqual(gkc.summarize(report)["route_lengths"]["missed"], 1)

    def test_words_with_no_amount_match_by_their_words(self) -> None:
        rows = [self.road("x", "k", "a", "A few days")]
        report = gkc.check(backup(self.PLACES, rows), self.KEY)
        self.assertEqual(report.route_lengths["found"], 1)

    def test_a_lore_only_run_never_scores_the_sessions_lengths(self) -> None:
        report = gkc.check(backup([], []), load("master_key.json"), docs="lore")
        self.assertEqual(report.route_lengths, Counter())
        both = gkc.check(backup([], []), load("master_key.json"), docs="both")
        self.assertEqual(both.route_lengths["missed"], 2)

    def test_the_sample_keys_roads_and_lengths(self) -> None:
        for name in ("session_key.json", "master_key.json"):
            key = load(name)
            roads = sorted((p["a"], p["b"]) for p in key["pairs"] if p["relation"] == "route")
            self.assertEqual(
                roads,
                [("Easthaven", "Bryn Shander"), ("Easthaven", "Caer-Konig"),
                 ("Easthaven", "Good Mead"), ("Targos", "Bryn Shander"),
                 ("Targos", "Easthaven")],
            )  # fmt: skip
            self.assertEqual(
                [(x["from"], x["to"], x["words"]) for x in key["route_lengths"]],
                [("Targos", "Bryn Shander", "1 hour"), ("Targos", "Easthaven", "five hours")],
            )
            # (a distance to a mountain and one trip through a branch stay travel facts)
            self.assertEqual(sorted(m["line"] for m in key["map_context"]), [6, 171])


class RoadConditions(unittest.TestCase):
    """The sample keys state no road condition (#4258 4a): the line is always there, and any row,
    a route place's or a road's, is a false note."""

    def test_none_expected_and_every_row_is_extra(self) -> None:
        key = {"nodes": [{"name": "Pass", "kind": "place", "aliases": [], "note": ""}], "pairs": []}
        data = backup([("p", "Pass", "place")], [])
        self.assertIn(
            "Place condition: found 0, wrong 0, missed 0, extra 0", gkc.render(gkc.check(data, key))
        )
        rows = [
            {"table": "map_fact", "id": i, "entity_id": place, "relation_id": road,
             "layer": "condition", "value": "closed", "faction_id": None, "status": "read",
             "to_session_at": None}
            for i, place, road in (("f1", "p", None), ("f2", None, "r1"))
        ]  # fmt: skip
        report = gkc.check(backup([("p", "Pass", "place")], rows), key)
        self.assertEqual(report.map_facts["condition"]["extra"], 2)


class StoryThreads(unittest.TestCase):
    """Story threads (#4453, quests slice 1; design §9.7): found by their people and part words,
    wrong by kind, doubles, extras, hints never missed, and the invented quest cases' must-nots."""

    def rel(self, i: str, who: str, thread: str, part: str) -> dict[str, Any]:
        return {
            "table": "relation", "id": i, "subject_id": who, "predicate": "in_thread",
            "object_id": thread, "detail": part, "status": "proposed",
        }  # fmt: skip

    def test_found_wrong_double_extra_and_hint(self) -> None:
        key = {
            "nodes": [{"name": n, "kind": "person", "aliases": [], "note": ""}
                      for n in ("Hlin", "Sephek", "Cerric", "Syllar", "Wit")],
            "pairs": [],
            "threads": [
                {"id": "t1", "kind": "quest", "name": "x", "giver": "Hlin", "target": "Sephek",
                 "taker": "", "missing": [], "in_danger": [], "must_find": True, "hint": False},
                {"id": "t2", "kind": "quest", "name": "y", "giver": "", "target": "",
                 "taker": "Cerric", "missing": ["Syllar"], "in_danger": [], "must_find": True,
                 "hint": False},
                {"id": "t3", "kind": "mystery", "name": "z", "giver": "", "target": "",
                 "taker": "", "missing": ["Wit"], "in_danger": [], "must_find": False,
                 "hint": True},
            ],
        }  # fmt: skip
        people = [("h", "Hlin", "npc"), ("s", "Sephek", "npc"), ("c", "Cerric", "npc"),
                  ("y", "Syllar", "npc"), ("w", "Wit", "npc")]  # fmt: skip
        threads = [
            ("q1", "Hlin's job: Sephek", "quest"), ("q2", "again", "quest"),
            ("q3", "Cerric's search", "hook"), ("q4", "something else", "quest"),
        ]  # fmt: skip
        rows = [
            self.rel("r1", "h", "q1", "quest giver"), self.rel("r2", "s", "q1", "target"),
            self.rel("r3", "h", "q2", "quest giver"), self.rel("r4", "s", "q2", "target"),
            self.rel("r5", "c", "q3", "takes it on"),
        ]  # fmt: skip
        report = gkc.check(backup(people + threads, rows), key)
        self.assertEqual(
            dict(report.threads),
            {"found": 1, "doubles": 1, "wrong": 1, "extra": 1, "hints found": 0},
        )
        self.assertIn("Story threads: found 1, missed 0, wrong 1", gkc.render(report))
        self.assertEqual(report.extra_entries, Counter())  # (threads are never extra names)

    def test_parts_and_branches_on_the_matched_thread(self) -> None:
        """#4500, quests slice 3: a part is found on the thread its key thread matched, "wrong
        thread" on another, extra when the key doesn't list it; a branch by its `branch_of`."""
        key = {
            "nodes": [{"name": n, "kind": "person", "aliases": [], "note": ""}
                      for n in ("Hlin", "Sephek", "Cerric", "Wit", "Okthan")],
            "pairs": [],
            "threads": [
                {"id": "t1", "kind": "quest", "name": "x", "giver": "Hlin", "target": "Sephek",
                 "taker": "", "missing": [], "in_danger": [], "must_find": True, "hint": False,
                 "parts": [{"kind": "objective", "words": ["capture or eliminate"]},
                           {"kind": "reward", "words": ["gold"]},
                           {"kind": "time_limit", "words": ["new moon"]}]},
                {"id": "t2", "kind": "quest", "name": "y", "giver": "", "target": "",
                 "taker": "Cerric", "missing": [], "in_danger": [], "must_find": True,
                 "hint": False, "parts": [{"kind": "complication", "words": ["Brotherhood"]}]},
                {"id": "t3", "kind": "mystery", "name": "z", "giver": "", "target": "",
                 "taker": "", "missing": ["Wit"], "in_danger": [], "must_find": False,
                 "hint": True, "branch_of": "t2"},
                {"id": "t4", "kind": "quest", "name": "w", "giver": "", "target": "",
                 "taker": "Okthan", "missing": [], "in_danger": [], "must_find": True,
                 "hint": False, "branch_of": "t2"},
            ],
        }  # fmt: skip
        people = [("h", "Hlin", "npc"), ("s", "Sephek", "npc"), ("c", "Cerric", "npc"),
                  ("w", "Wit", "npc"), ("o", "Okthan", "npc")]  # fmt: skip
        threads = [("q1", "Hlin's job", "quest"), ("q2", "Cerric's search", "quest"),
                   ("q3", "Who killed Wit?", "mystery"), ("q4", "Okthan's", "quest")]  # fmt: skip

        def part(i: str, thread: str, kind: str, words: str) -> dict[str, Any]:
            return {"table": "thread_part", "id": i, "thread_id": thread, "kind": kind,
                    "words": words, "status": "confirmed", "to_session_at": None}  # fmt: skip

        rows = [
            self.rel("r1", "h", "q1", "quest giver"), self.rel("r2", "s", "q1", "target"),
            self.rel("r3", "c", "q2", "takes it on"), self.rel("r4", "w", "q3", "the one missing"),
            self.rel("r5", "o", "q4", "takes it on"),
            {"table": "relation", "id": "b1", "subject_id": "q3", "predicate": "branch_of",
             "object_id": "q2", "status": "confirmed"},
            part("p1", "q1", "objective", "Capture or eliminate Sephek"),
            part("p2", "q2", "reward", "100 gold"),  # (t1's reward, on Cerric's thread)
            part("p3", "q2", "complication", "the Brotherhood knows him"),
            part("p4", "q1", "help", "a sled"),  # (the key doesn't list it)
        ]  # fmt: skip
        report = gkc.check(backup(people + threads, rows), key)
        self.assertEqual(
            dict(report.thread_parts),
            {"found objective": 1, "found complication": 1, "wrong thread": 1,
             "missed time_limit": 1, "extra": 2, "branches found": 1, "branches missed": 1},
        )  # fmt: skip
        self.assertIn("Thread parts found: objective 1/1, complication 1/1", gkc.render(report))

    def test_clues_on_the_matched_thread(self) -> None:
        """#4624, quests 6b: a clue tied to the matched thread by words, one on another thread,
        a good find missed, an extra tie, and a must-not thread with a tie (false)."""
        key = {
            "nodes": [{"name": n, "kind": "person", "aliases": [], "note": ""}
                      for n in ("Hlin", "Sephek", "Cerric")],
            "pairs": [],
            "threads": [
                {"id": "t1", "kind": "quest", "name": "x", "giver": "Hlin", "target": "Sephek",
                 "taker": "", "missing": [], "in_danger": [], "must_find": True, "hint": False},
                {"id": "t2", "kind": "quest", "name": "y", "giver": "", "target": "",
                 "taker": "Cerric", "missing": [], "in_danger": [], "must_find": True,
                 "hint": False},
            ],
            "clues": [
                {"thread": "t2", "words": ["Tome"]},
                {"thread": "t2", "words": ["journal"]},
                {"thread": "t2", "words": ["whisper"], "must_find": False},
            ],
            "must_not_clues": [{"thread": "t1"}],
        }  # fmt: skip
        people = [("h", "Hlin", "npc"), ("s", "Sephek", "npc"), ("c", "Cerric", "npc")]
        things = [("q1", "Hlin's job", "quest"), ("q2", "Cerric's search", "quest"),
                  ("k1", "the warning in the azure tome", "clue"), ("k2", "his journal", "clue"),
                  ("k3", "a strange mark", "clue")]  # fmt: skip

        def tie(i: str, clue: str, thread: str) -> dict[str, Any]:
            return {"table": "relation", "id": i, "subject_id": clue, "predicate": "clue_to",
                    "object_id": thread, "status": "confirmed"}  # fmt: skip

        rows = [
            self.rel("r1", "h", "q1", "quest giver"), self.rel("r2", "s", "q1", "target"),
            self.rel("r3", "c", "q2", "takes it on"),
            tie("c1", "k1", "q2"), tie("c2", "k2", "q1"), tie("c3", "k3", "q2"),
        ]  # fmt: skip
        report = gkc.check(backup(people + things, rows), key)
        self.assertEqual(
            dict(report.clues),
            {"found": 1, "wrong thread": 1, "good finds missed": 1, "extra": 1, "false": 1},
        )
        self.assertIn("Clues: found 1, missed 0, wrong thread 1", gkc.render(report))
        self.assertEqual(report.extra_entries["clue"], 0)  # (a tied clue is scored here only)

    def test_a_clue_answers_one_row_and_words_match_from_their_start(self) -> None:
        key = {
            "nodes": [{"name": "Cerric", "kind": "person", "aliases": [], "note": ""}],
            "pairs": [],
            "threads": [{"id": "t2", "kind": "quest", "name": "y", "giver": "", "target": "",
                         "taker": "Cerric", "missing": [], "in_danger": [], "must_find": True,
                         "hint": False}],
            "clues": [
                {"thread": "t2", "words": ["coming"], "must_find": False},
                {"thread": "t2", "words": ["tome"]},  # (must-find rows are answered first)
                {"thread": "t2", "words": ["Wit"]},  # ("with" is not "Wit": missed)
                {"thread": "t9", "words": ["map"]},  # (no such thread: missed)
            ],
        }  # fmt: skip
        things = [("c", "Cerric", "npc"), ("q2", "Cerric's search", "quest"),
                  ("k1", "the tome's warning: they are coming, with blood", "clue")]  # fmt: skip
        rows = [
            self.rel("r3", "c", "q2", "takes it on"),
            {"table": "relation", "id": "c1", "subject_id": "k1", "predicate": "clue_to",
             "object_id": "q2", "status": "confirmed"},
        ]  # fmt: skip
        report = gkc.check(backup(things, rows), key)
        got = {k: n for k, n in report.clues.items() if n}
        self.assertEqual(got, {"found": 1, "missed": 2, "good finds missed": 1})

    def test_the_keys_clue_rows_name_their_threads(self) -> None:
        for name in ("lore_key.json", "master_key.json"):
            key = load(name)
            ids = {t["id"] for t in key["threads"]}
            for c in [*key["clues"], *key["must_not_clues"]]:
                self.assertIn(c["thread"], ids)

    def test_progress_on_the_matched_thread(self) -> None:
        """#4522, quests 5c: what the session moved on a lore thread, read off the backup thread
        its key thread matched: the thread's open state, a part's state now, a session's new part
        (or an `or` row); a good find's miss apart; a must-not that stands is false."""
        key = {
            "nodes": [{"name": n, "kind": "person", "aliases": [], "note": ""}
                      for n in ("Keegan", "Garret")],
            "pairs": [],
            "threads": [
                {"id": "t1", "kind": "quest", "name": "x", "giver": "Keegan", "target": "Garret",
                 "taker": "", "missing": [], "in_danger": [], "must_find": True, "hint": False},
            ],
            "progress": [
                {"thread": "t1", "change": "thread_moves", "state": "happening_now"},
                {"thread": "t1", "change": "part_moves", "part_kind": "reward",
                 "words": ["50 gold"], "state": "settled",
                 "or": [{"change": "new_part", "part_kind": "reward_given", "words": ["50"]}]},
                {"thread": "t1", "change": "new_part", "part_kind": "help", "words": ["SLED"],
                 "state": "lent"},  # (only a rejected and a closed one lent: missed)
                {"thread": "t1", "change": "new_part", "part_kind": "time_limit",
                 "words": ["days"], "must_find": False},
                {"thread": "t9", "change": "thread_moves", "state": "finished"},
            ],
            "must_not_progress": [
                {"thread": "t1", "change": "part_moves", "part_kind": "objective",
                 "words": ["Garret"], "state": "done"},
                {"thread": "t1", "change": "thread_moves", "state": "finished"},
            ],
        }  # fmt: skip

        def part(i: str, kind: str, words: str, state: str, source: str) -> dict[str, Any]:
            return {"table": "thread_part", "id": i, "thread_id": "q1", "kind": kind,
                    "words": words, "state": state, "source": source, "status": "confirmed",
                    "to_session_at": None}  # fmt: skip

        rows = [
            self.rel("r1", "k", "q1", "quest giver"), self.rel("r2", "g", "q1", "target"),
            {"table": "state", "id": "s1", "entity_id": "q1", "condition": "resolved",
             "closed_at": 5},  # (closed: the "finished" must-not doesn't stand on it)
            {"table": "state", "id": "s2", "entity_id": "q1", "condition": "active",
             "closed_at": None},
            part("p1", "reward_given", "50 gold up front", "given", "session"),  # (the "or")
            part("p2", "help", "two sleds", "to_keep", "session"),  # (not lent: missed)
            part("p3", "objective", "find Garret", "done", "lore"),  # (a must-not that stands)
            part("p4", "time_limit", "ten DAYS", "running", "lore"),  # (not the session's own)
            {**part("p5", "help", "two Sleds", "lent", "session"), "status": "rejected"},
            {**part("p6", "help", "a sled", "lent", "session"), "to_session_at": 9},
        ]  # fmt: skip
        people = [("k", "Keegan", "npc"), ("g", "Garret", "npc"), ("q1", "Find him", "quest")]
        report = gkc.check(backup(people, rows), key)
        self.assertEqual(
            dict(report.progress),
            {"found": 2, "missed": 2, "good finds missed": 1, "false": 1},
        )
        self.assertIn("Thread progress: found 2, missed 2, false 1", gkc.render(report))
        self.assertEqual(gkc.summarize(report)["progress"]["found"], 2)

    def test_a_found_part_move_and_a_double_thread(self) -> None:
        """A part's own move is found (not only through an "or"), and on the second backup thread
        a key thread matched as well as on the first."""
        key = {
            "nodes": [{"name": n, "kind": "person", "aliases": [], "note": ""}
                      for n in ("Keegan", "Garret")],
            "pairs": [],
            "threads": [{"id": "t1", "kind": "quest", "name": "x", "giver": "Keegan",
                         "target": "Garret", "taker": "", "missing": [], "in_danger": [],
                         "must_find": True, "hint": False}],
            "progress": [{"thread": "t1", "change": "part_moves", "part_kind": "reward",
                          "words": ["Gold"], "state": "settled"}],
        }  # fmt: skip
        rows = [
            self.rel("r1", "k", "q1", "quest giver"), self.rel("r2", "g", "q1", "target"),
            self.rel("r3", "k", "q2", "quest giver"), self.rel("r4", "g", "q2", "target"),
            {"table": "thread_part", "id": "p1", "thread_id": "q2", "kind": "reward",
             "words": "50 gold", "state": "settled", "source": "lore", "status": "confirmed",
             "to_session_at": None},
        ]  # fmt: skip
        people = [("k", "Keegan", "npc"), ("g", "Garret", "npc"),
                  ("q1", "Find him", "quest"), ("q2", "Find him again", "quest")]  # fmt: skip
        report = gkc.check(backup(people, rows), key)
        self.assertEqual(dict(report.progress), {"found": 1})

    def test_the_master_keys_progress_rows_are_ones_the_reader_can_make(self) -> None:
        """A typo in a row would only ever be scored as missed: each names a lore thread of the
        key, a change the reader makes, a part kind and a state that kind has."""
        from dmbot.memory.session_progress import THREAD_WORDS
        from dmbot.memory.thread_parts import STATES

        key = load("master_key.json")
        ids = {t["id"] for t in key["threads"]}
        rows = [*key["progress"], *key["must_not_progress"]]
        self.assertTrue(rows)
        for p in rows:
            self.assertIn(p["thread"], ids)
            for r in (p, *p.get("or", [])):
                with self.subTest(p.get("id", p["note"]), change=r["change"]):
                    if r["change"] == "thread_moves":
                        self.assertIn(r["state"], THREAD_WORDS)
                        continue
                    self.assertIn(r["change"], ("part_moves", "new_part"))
                    self.assertIn(r["part_kind"], STATES)
                    self.assertTrue(r["words"])
                    if r.get("state"):
                        self.assertIn(r["state"], STATES[r["part_kind"]])

    def test_the_quest_cases_key_reads_and_scores_its_must_nots(self) -> None:
        key = load("quest_cases_key.json")
        self.assertEqual(len(key["threads"]), 5)
        threads = [("q1", "Head to Highmoor at first light", "quest")]
        report = gkc.check(backup([("p", "Old Pell", "npc"), *threads], []), key)
        self.assertEqual(report.threads["false"], 1)
        self.assertEqual(report.threads["missed"], 5)
