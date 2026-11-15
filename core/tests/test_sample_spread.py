"""The five-run sample (#3545; Supervisor's rulings of 2026-11-15): each count as median, range and
spread, a move counted only above the larger spread, a varying sure step flagged when its input
didn't vary, and the questions the DM answered sorted by what became of them. Pure, invented."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dmbot.devtools.sample_run import questions as q
from dmbot.devtools.sample_run import spread


class Spread(unittest.TestCase):
    def test_median_range_and_spread(self) -> None:
        runs = [{"ties found": n} for n in (38, 57, 42, 51, 43)]
        stats = spread.summarize(runs)
        s = stats["ties found"]
        self.assertEqual((s.median, s.low, s.high, s.spread, s.runs), (43, 38, 57, 19, 5))
        self.assertEqual(
            spread.render(stats), ["ties found: median 43 (38 to 57, spread 19, 5 runs)"]
        )

    def test_a_move_counts_only_above_the_larger_spread(self) -> None:
        before = spread.summarize([{"a": 10}, {"a": 12}, {"a": 11}, {"b": 5}, {"b": 5}])
        now = spread.summarize([{"a": 13}, {"a": 14}, {"a": 12}, {"b": 9}, {"b": 9}])
        moved = spread.compare(now, before)
        self.assertEqual(
            moved,
            ["a: 11 → 13 (+2; spreads 2 and 2): noise", "b: 5 → 9 (+4; spreads 0 and 0): changed"],
        )

    def test_counts_words_and_money(self) -> None:
        got = spread.step_metrics("AI decides (lore)", {"calls": 3, "cost": "$0.04", "stage": "x"})
        self.assertEqual(got, {"AI decides (lore): calls": 3.0, "AI decides (lore): cost": 0.04})

    def test_the_sure_step_varying_on_the_same_read_is_flagged(self) -> None:
        same_read = [
            {"lore read: names": 60, "sure (lore): accepted": 30},
            {"lore read: names": 60, "sure (lore): accepted": 31},
        ]
        self.assertEqual(spread.sure_flags(spread.summarize(same_read)), ["sure (lore): accepted"])
        other_read = [
            {"lore read: names": 60, "sure (lore): accepted": 30},
            {"lore read: names": 62, "sure (lore): accepted": 31},
        ]
        self.assertEqual(spread.sure_flags(spread.summarize(other_read)), [])  # (it followed)

    def test_the_sessions_sure_step_follows_the_ais_lore_decisions(self) -> None:
        runs = [
            {"lore read: names": 60, "AI decides (lore): calls": c, "session import: lines": 172,
             "sure (session): accepted": 30 + c}
            for c in (3, 4)
        ]  # fmt: skip
        self.assertEqual(spread.sure_flags(spread.summarize(runs)), [])

    def test_a_summary_comes_back_as_it_was_saved(self) -> None:
        stats = spread.summarize([{"a": 1}, {"a": 3}])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "summary.json")
            spread.save(path, stats, commit="abc")
            self.assertEqual(spread.load(path), ("abc", stats))


def _state(**rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    empty = ("entities", "relations", "links", "claims", "map_facts", "languages", "traits",
             "rule_links")  # fmt: skip
    return {k: list(rows.get(k, [])) for k in empty}


def _before(state: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    """What `questions.waiting` gives, built from plain rows (it reads the stores' lists)."""
    return {
        q.NAMES: [
            {"id": r["id"], "type": r["type"], "name": r["name"]}
            for r in state["entities"] if r["status"] == "proposed"
        ],
        q.CONNECTIONS: [
            {"s": r["subject_id"], "p": r["predicate"], "o": r["object_id"]}
            for r in state["links"]
        ],
        q.PLACES: [
            {"id": r["id"], "place": r["entity_id"], "layer": r["layer"], "value": r["value"]}
            for r in state["map_facts"]
        ],
    }  # fmt: skip


class Answered(unittest.TestCase):
    def test_things_said_languages_and_lineage(self) -> None:
        before = {
            q.FACTS: [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}],
            q.LANGUAGES: [{"id": "l1"}, {"id": "l2"}],
            q.TRAITS: [
                {"who": "e1", "kind": "class", "key": "wizard"},
                {"who": "e1", "kind": "species", "key": "halfelf"},  # (models.name_key)
                {"who": "e2", "kind": "language", "key": "elvish"},
                {"who": "e2", "kind": "class", "key": "rogue"},
            ],
        }
        now = _state(
            claims=[{"id": "c1", "status": "confirmed"}, {"id": "c2", "status": "dropped"},
                    {"id": "c3", "status": "rejected"}],
            languages=[{"id": "l1", "entity_id": "e1", "name_key": "dwarvish", "given_by": "dm"},
                       {"id": "lx", "entity_id": "e2", "name_key": "elvish", "given_by": "dm"}],
            rule_links=[{"entity_id": "e1", "kind": "class", "name": "Fighter"},
                        {"entity_id": "e1", "kind": "class", "name": "Wizard"},
                        {"entity_id": "e1", "kind": "species", "name": "Half-Elf"}],
            traits=[{"entity_id": "e2", "kind": "class", "name_key": "rogue", "state": "never"}],
        )  # fmt: skip
        got = q.classify(before, now)
        self.assertEqual(dict(got[q.FACTS]), {q.KEPT: 1, q.TURNED_DOWN: 1})  # (dropped: no answer)
        self.assertEqual(dict(got[q.LANGUAGES]), {q.KEPT: 1, q.TURNED_DOWN: 1})
        self.assertEqual(dict(got[q.TRAITS]), {q.KEPT: 3, q.TURNED_DOWN: 1})

    def test_a_turned_down_lineage_beside_a_kept_one_is_not_changed(self) -> None:
        before = {q.PLACES: [{"id": "m1", "place": "p", "layer": "culture", "value": "elf"}]}
        now = _state(map_facts=[
            {"id": "m1", "entity_id": "p", "layer": "culture", "value": "elf",
             "faction_id": None, "status": "rejected"},
            {"id": "m2", "entity_id": "p", "layer": "culture", "value": "dwarf",
             "faction_id": None, "status": "confirmed"},
        ])  # fmt: skip
        self.assertEqual(dict(q.classify(before, now)[q.PLACES]), {q.TURNED_DOWN: 1})

    def test_the_snapshot_is_owner_only_and_a_stray_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "questions.json")
            q.save(path, {"campaign": "c", "guild": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(q.load(path), {"campaign": "c", "guild": 1})
            Path(d, "stray.json").write_text("not json")
            self.assertIsNone(q.load(Path(d, "stray.json")))

    def test_each_kind_sorted_by_what_the_dm_did(self) -> None:
        before = _before(
            _state(
                entities=[
                    {"id": i, "type": "person", "name": n, "status": "proposed",
                     "merged_into": None}
                    for i, n in (("e1", "Tamsin Vell"), ("e2", "Oro"), ("e3", "Brin"),
                                 ("e4", "Dask"), ("e5", "Hale"))
                ],
                links=[
                    {"subject_id": "e1", "predicate": "sibling_of", "object_id": "e2"},
                    {"subject_id": "e1", "predicate": "ally_of", "object_id": "e3"},
                    {"subject_id": "e2", "predicate": "enemy_of", "object_id": "e3"},
                ],
                map_facts=[
                    {"id": "m1", "entity_id": "p1", "layer": "climate", "value": "cold",
                     "faction_id": None, "status": "read"},
                    {"id": "m2", "entity_id": "p2", "layer": "climate", "value": "cold",
                     "faction_id": None, "status": "read"},
                ],
            )
        )  # fmt: skip
        now = _state(
            entities=[
                {"id": "e1", "type": "person", "name": "Tamsin Vell", "status": "confirmed",
                 "merged_into": None},  # kept as suggested
                {"id": "e2", "type": "creature", "name": "Oro", "status": "confirmed",
                 "merged_into": None},  # changed (another kind)
                {"id": "e3", "type": "person", "name": "Brin", "status": "rejected",
                 "merged_into": None},  # turned down
                {"id": "e4", "type": "person", "name": "Dask", "status": "proposed",
                 "merged_into": None},  # not answered
                {"id": "e5", "type": "person", "name": "Hale", "status": "merged",
                 "merged_into": "e1"},  # changed (the same as another entry)
            ],
            relations=[
                {"subject_id": "e1", "predicate": "sibling_of", "object_id": "e2",
                 "status": "confirmed"},  # kept
                {"subject_id": "e1", "predicate": "friend_of", "object_id": "e3",
                 "status": "confirmed"},  # changed
            ],
            map_facts=[
                {"id": "m1", "entity_id": "p1", "layer": "climate", "value": "cold",
                 "faction_id": None, "status": "confirmed"},
                {"id": "m2", "entity_id": "p2", "layer": "climate", "value": "cold",
                 "faction_id": None, "status": "rejected"},
                {"id": "m3", "entity_id": "p2", "layer": "climate", "value": "arctic",
                 "faction_id": None, "status": "confirmed"},
            ],
        )  # fmt: skip
        got = q.classify(before, now)
        self.assertEqual(dict(got[q.NAMES]), {q.KEPT: 1, q.CHANGED: 2, q.TURNED_DOWN: 1, q.OPEN: 1})
        self.assertEqual(dict(got[q.CONNECTIONS]), {q.KEPT: 1, q.CHANGED: 1, q.TURNED_DOWN: 1})
        self.assertEqual(dict(got[q.PLACES]), {q.KEPT: 1, q.CHANGED: 1})
        lines = q.lines(got)
        self.assertIn(
            "names: 5 asked; kept as suggested 1, changed 2, turned down 1, not answered yet 1"
            " (25% right)",
            lines,
        )
        for name in ("Tamsin", "Oro", "Brin"):  # (counts only)
            self.assertNotIn(name, "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
