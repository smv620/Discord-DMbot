"""The readings key and its score (#4655, quests slice 7a): the invented guess cases (a session with
one mystery, six guesses and four lines that must make none) and the key check's "Readings:" line:
found, missed, wrong holder and false. No database, no AI. Invented names."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.devtools import graph_key_check as gkc
from tests.test_graph_key_check import FIXTURES, backup, rel

KEY: dict[str, Any] = json.loads((FIXTURES / "guess_cases_key.json").read_text())
SESSION = (FIXTURES / "guess_cases_session.txt").read_text().splitlines()
LORE = (FIXTURES / "guess_cases_lore.txt").read_text().splitlines()


class TheKey(unittest.TestCase):
    def test_every_row_names_key_entries_and_a_line_of_the_session(self) -> None:
        nodes = {n["name"] for n in KEY["nodes"]}
        ids = [r["id"] for r in KEY["readings"] + KEY["must_not_readings"]]
        self.assertEqual(len(ids), len(set(ids)))
        for r in KEY["readings"]:
            self.assertIn(r["subject"], nodes, r["id"])
            self.assertIn(r["object"], nodes, r["id"])
            self.assertTrue(r["who"] == "party" or r["who"] in nodes, r["id"])
            self.assertEqual(r["mystery"], KEY["threads"][0]["id"])
            said = SESSION[r["line"] - 1]
            self.assertIn(r["object"].split()[0].lower(), said.lower(), r["id"])

    def test_the_cases_cover_what_the_slice_must_score(self) -> None:
        holders = [r["who"] for r in KEY["readings"]]
        self.assertIn("party", holders)  # (the whole table agreed)
        self.assertGreaterEqual(len(set(holders) - {"party"}), 4)  # (each character guesses)
        self.assertEqual(len(KEY["must_not_readings"]), 4)  # (a joke, a question, a hint, a plan)
        self.assertIn("mystery", KEY["threads"][0]["kind"])
        self.assertIn(KEY["threads"][0]["name"].split("?")[0].split()[-1], LORE[4])

    def test_the_session_uses_the_sample_run_s_speakers(self) -> None:
        speakers = {line.split("] ", 1)[1].split(":", 1)[0] for line in SESSION}
        self.assertEqual(speakers, {"DM", "player_ist", "player_jac", "player_eri", "player_owe"})


ENTRIES = [
    ("mira", "Mira Vell", "character"), ("rusk", "Captain Rusk", "character"),
    ("well", "Old Well", "place"), ("aldo", "Brother Aldo", "character"),
    ("oswin", "Oswin", "character"), ("cerric", "Cerric Vanya'elen", "character"),
    ("bel", "Belleros Stormgrace", "character"),
]  # fmt: skip


def belief(bid: str, view: str, relation: str, status: str = "theory") -> dict[str, Any]:
    return {"table": "belief", "id": bid, "perspective_id": view, "relation_id": relation,
            "status": status, "confidence": 0.5}  # fmt: skip


def claim(cid: str, view: str, subject: str, obj: str, status: str = "theory") -> dict[str, Any]:
    reading = {"view": view, "status": status, "confidence": 0.5}
    return {"table": "claim", "id": cid, "status": "proposed", "subject": subject,
            "object": obj, "reading": reading}  # fmt: skip


VIEWS: list[dict[str, Any]] = [
    {"table": "perspective", "id": "v-party", "kind": "party", "entity_id": None},
    {"table": "perspective", "id": "v-cerric", "kind": "pc", "entity_id": "cerric"},
    {"table": "perspective", "id": "v-bel", "kind": "pc", "entity_id": "bel"},
]


class TheScore(unittest.TestCase):
    def score(self, rows: list[dict[str, Any]]) -> gkc.Report:
        return gkc.check(backup(ENTRIES, [*VIEWS, *rows]), KEY)

    def test_found_wrong_holder_missed_and_false(self) -> None:
        rows = [
            # g01: Cerric's guess, kept as a belief in Cerric's view: found
            rel("r1", "mira", "poisoned", "well", status="proposed"),
            belief("b1", "v-cerric", "r1"),
            # g02: Belleros's accusation, waiting as a reading: found
            claim("c1", "pc:Belleros Stormgrace", "Captain Rusk", "Old Well", "accusation"),
            # g06: the party's guess, but kept in Cerric's view only: wrong holder
            rel("r2", "aldo", "hired", "oswin", status="proposed"),
            belief("b2", "v-cerric", "r2"),
            # gn2: the question "did Elder Hobb poison it?" made a guess: false
            claim("c2", "pc:Belleros Stormgrace", "Elder Hobb", "Old Well"),
        ]
        report = self.score(rows)
        self.assertEqual(
            dict(report.readings), {"found": 2, "wrong holder": 1, "missed": 3, "false": 1}
        )
        self.assertIn("Readings: found 2, missed 3, wrong holder 1, false 1", gkc.render(report))

    def test_the_truth_or_a_kept_fact_is_never_a_guess(self) -> None:
        truth: dict[str, Any] = {
            "table": "perspective", "id": "v-truth", "kind": "truth", "entity_id": None
        }  # fmt: skip
        rows = [
            truth,
            rel("r1", "mira", "poisoned", "well"),
            belief("b1", "v-truth", "r1", status="secret_plot"),
        ]
        self.assertEqual(self.score(rows).readings["found"], 0)

    def test_a_short_name_as_the_model_wrote_it_is_found(self) -> None:
        rows = [claim("c1", "pc:Belleros", "Captain Rusk", "the Old Well", "accusation")]
        self.assertEqual(self.score(rows).readings["found"], 1)

    def test_a_party_reading_waiting_is_found(self) -> None:
        rows = [claim("c1", "party", "Brother Aldo", "Oswin the miller")]
        self.assertEqual(self.score(rows).readings["found"], 1)

    def test_a_guess_the_dm_turned_down_is_not_counted(self) -> None:
        rows = [
            rel("r1", "mira", "poisoned", "well", status="rejected"),
            belief("b1", "v-cerric", "r1"),
        ]
        report = self.score(rows)
        self.assertEqual((report.readings["found"], report.readings["missed"]), (0, 6))

    def test_a_guess_in_everyone_s_view_is_no_table_guess(self) -> None:
        everyone: dict[str, Any] = {
            "table": "perspective", "id": "v-all", "kind": "everyone", "entity_id": None
        }  # fmt: skip
        rows = [everyone, rel("r1", "mira", "poisoned", "well"), belief("b1", "v-all", "r1")]
        report = self.score(rows)
        self.assertEqual(report.readings["found"] + report.readings["false"], 0)

    def test_must_not_rows_name_a_line_of_the_session(self) -> None:
        nodes = {n["name"] for n in KEY["nodes"]}
        for r in KEY["must_not_readings"]:
            said = SESSION[r["line"] - 1].lower()
            named = [n for n in (r["subject"], r["object"]) if n in nodes]
            self.assertTrue(any(n.split()[-1].lower() in said for n in named), r["id"])

    def test_a_key_without_readings_prints_no_line(self) -> None:
        key = {k: v for k, v in KEY.items() if k not in ("readings", "must_not_readings")}
        report = gkc.check(backup(ENTRIES, VIEWS), key)
        self.assertNotIn("Readings:", gkc.render(report))
