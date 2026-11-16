"""Each key row's tier from the backup and the decision record (#4523 slice 2, the tier table and
rulings E and F as Supervisor ruled), and a secret other name's knowers. Pure: small invented
backups and records."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from dmbot.devtools import graph_key_check as gkc
from dmbot.devtools import key_knowers
from dmbot.devtools import key_tiers as kt
from tests.test_graph_key_check import rel
from tests.test_key_knowers import backup, entity, key, knower


def said(kind: str, decision: str, *names: str, **more: Any) -> dict[str, Any]:
    """One decision record line, as `decide.Asker` writes it."""
    return {"stage": "lore", "kind": kind, "item": "x", "decision": decision, "names": list(names),
            "offered": [], "first": "", "picked": "", "done": True, **more}  # fmt: skip


def tiers(data: dict[str, Any], record: list[dict[str, Any]], the_key: Any = None) -> gkc.Report:
    return gkc.check(data, the_key or key(["Mira", "Ulf"]), "both", record)


class TheTable(unittest.TestCase):
    """`tier` on its own: the ruling's table, row by row."""

    def test_found_and_never_asked_is_dmbots_own(self) -> None:
        self.assertEqual(kt.tier(True, False, []), kt.TOTAL)
        self.assertEqual(kt.tier(True, False, [said("names", "not sure")]), kt.TOTAL)

    def test_a_yes_to_dmbots_first_choice_is_partial(self) -> None:
        self.assertEqual(kt.tier(True, False, [said("things said", "yes")]), kt.PARTIAL)
        first = said("which one", "yes", offered=["A", "B"], first="A", picked="A")
        self.assertEqual(kt.tier(True, False, [first]), kt.PARTIAL)

    def test_another_offered_choice_is_provisional(self) -> None:
        other = said("which one", "yes", offered=["A", "B"], first="A", picked="B")
        self.assertEqual(kt.tier(True, False, [other]), kt.PROVISIONAL)

    def test_a_leak_is_a_failure_however_it_came(self) -> None:
        self.assertEqual(kt.tier(True, True, []), kt.FAILURE)

    def test_a_no_to_a_key_row_is_the_ai_dms_miss_not_a_tier(self) -> None:
        self.assertEqual(kt.tier(False, False, [said("names", "no")]), kt.AI_SAID_NO)

    def test_left_at_the_cap_is_not_decided(self) -> None:
        self.assertEqual(kt.tier(False, False, [said("names", kt.NOT_DECIDED)]), kt.NOT_DECIDED)

    def test_missing_otherwise_is_a_failure(self) -> None:
        self.assertEqual(kt.tier(False, False, []), kt.FAILURE)
        self.assertEqual(kt.tier(False, False, [said("names", "not sure")]), kt.FAILURE)
        refused = said("names", "no", done=False)  # (a rule refused it: it is not an answer)
        self.assertEqual(kt.tier(False, False, [refused]), kt.FAILURE)


class InACheck(unittest.TestCase):
    def test_a_which_one_pick_of_the_second_candidate_is_provisional(self) -> None:
        pick = said("which one", "yes", "Mira", "brother", offered=["Bram", "Ulf"], first="Bram",
                    picked="Ulf")  # fmt: skip
        data = backup([knower("mira", relation="r1"), knower("ulf", relation="r1")])
        report = tiers(data, [pick])
        self.assertEqual(report.tiers[kt.PROVISIONAL], 1)  # (the tie)
        self.assertEqual(report.tiers[kt.TOTAL], 4)  # (the four entries, never asked)

    def test_a_turned_down_key_name_and_a_turned_down_extra(self) -> None:
        data = backup([])
        for row in data["sections"]["memory"]:
            if row.get("id") == "hall":
                row["status"] = "rejected"
        record = [said("names", "no", "Stonehall"), said("names", "no", "Dog sleds")]
        report = tiers(data, record)
        # the key row turned down: the stand-in's miss, by kind, outside the tiers
        self.assertEqual(report.tiers[kt.AI_SAID_NO], 1)
        self.assertEqual(report.ai_said_no, {"place": 1})
        # the extra turned down: DMbot's wrong suggestion caught, the owner's partial failure
        self.assertEqual(report.extras[kt.EXTRA_TURNED_DOWN], 1)
        self.assertEqual(report.tiers[kt.PARTIAL_FAILURE], 1)

    def test_an_extra_is_dmbots_own_or_kept_by_the_ai_dm(self) -> None:
        more = (entity("grey", "Greyford", kind="place"), entity("ash", "Ashwell", kind="place"))
        report = tiers(backup([], *more), [said("names", "yes", "Ashwell")])
        self.assertEqual(report.extras[kt.EXTRA_ITSELF], 2)  # (Greyford, and the backup's guild)
        self.assertEqual(report.tiers[kt.FAILURE], 2)  # (added by itself: failures; kept: none)
        self.assertEqual(report.extras[kt.EXTRA_KEPT], 1)

    def test_an_extra_tie_between_key_entries_is_counted_by_how_it_came(self) -> None:
        extra = rel("r9", "tavin", "knows", "ulf")
        report = tiers(backup([], extra), [])
        self.assertEqual(report.extras[kt.EXTRA_ITSELF], 2)  # (the tie, and the backup's guild)
        kept = tiers(backup([], extra), [said("things said", "yes", "Tavin", "Ulf")])
        self.assertEqual((kept.extras[kt.EXTRA_KEPT], kept.extras[kt.EXTRA_ITSELF]), (1, 1))

    def test_a_leaked_row_is_a_failure_in_the_tiers(self) -> None:
        rows = [knower(w, relation="r1") for w in ("mira", "ulf", "tavin")]
        report = tiers(backup(rows), [])
        self.assertEqual(report.tiers_must[kt.FAILURE], 1)  # (the tie: Tavin knows it)

    def test_left_at_the_cap_counts_apart(self) -> None:
        data = backup([])
        data["sections"]["memory"] = [r for r in data["sections"]["memory"] if r.get("id") != "r1"]
        report = tiers(data, [said("which one", kt.NOT_DECIDED, "Mira", "brother",
                                   offered=["Bram", "Ulf"], first="Bram")])  # fmt: skip
        self.assertEqual(report.tiers[kt.NOT_DECIDED], 1)

    def test_no_order_is_counted(self) -> None:
        report = tiers(backup([]), [said("names", "yes", "Stonehall", no_order=True)])
        self.assertEqual(report.unordered, 1)

    def waiting_hall(self) -> dict[str, Any]:
        data = backup([])
        for row in data["sections"]["memory"]:
            if row.get("id") == "hall":
                row["status"] = "proposed"
        return data

    def test_a_waiting_key_entry_is_not_in_the_graph_yet(self) -> None:
        """Left at the cap it is not decided; left "not sure" it is a failure, never DMbot's own."""
        capped = tiers(self.waiting_hall(), [said("names", kt.NOT_DECIDED, "Stonehall")])
        self.assertEqual(capped.tiers[kt.NOT_DECIDED], 1)
        unsure = tiers(self.waiting_hall(), [said("names", "not sure", "Stonehall")])
        # (Stonehall, and the backup's extra guild that DMbot added by itself)
        self.assertEqual((unsure.tiers[kt.FAILURE], unsure.tiers[kt.TOTAL]), (2, 4))

    def test_found_though_the_ai_dm_said_no_is_its_own_line(self) -> None:
        report = tiers(backup([]), [said("names", "no", "Stonehall")])
        self.assertEqual(report.tiers[kt.FOUND_DESPITE_NO], 1)

    def test_a_no_about_a_key_row_outside_the_documents_scored_is_no_turned_down_extra(
        self,
    ) -> None:
        the_key = key(["Mira", "Ulf"])
        the_key["nodes"].append({"name": "Bram", "kind": "person", "docs": ["backstory_x"],
                                 "knowers": "dm"})  # fmt: skip
        report = tiers(backup([]), [said("names", "no", "Bram")], the_key)
        self.assertEqual(report.extras[kt.EXTRA_TURNED_DOWN], 0)

    def test_a_look_alike_answer_is_never_about_a_name_or_an_extra(self) -> None:
        apart = said("look-alikes", "no", "unnamed - a guard", "Stonehall")
        report = tiers(self.waiting_hall(), [apart, said("which one", "no", "Mira", "kin",
                                                         offered=["Bram"])])  # fmt: skip
        self.assertEqual(report.tiers[kt.PARTIAL_FAILURE], 0)
        self.assertEqual(report.extras[kt.EXTRA_TURNED_DOWN], 0)

    def test_an_extra_kept_under_another_name_is_kept_by_the_ai_dm(self) -> None:
        alias = {"table": "alias", "id": "al9", "entity_id": "grey", "text": "Old Ford",
                 "key": "old ford", "status": "confirmed"}  # fmt: skip
        data = backup([], entity("grey", "Greyford", kind="place"), alias)
        report = tiers(data, [said("names", "yes", "Old Ford")])
        self.assertEqual(report.extras[kt.EXTRA_KEPT], 1)

    def test_a_pick_is_judged_by_its_place_not_its_name(self) -> None:
        twin = said("which one", "yes", offered=["Ulf", "Ulf"], first="Ulf", picked="Ulf",
                    picked_at=2)  # fmt: skip
        self.assertEqual(kt.tier(True, False, [twin]), kt.PROVISIONAL)

    def test_without_a_record_there_are_no_tiers(self) -> None:
        report = gkc.check(backup([]), key("party"))
        self.assertEqual(report.tiers, {})
        self.assertNotIn("tiers", gkc.summarize(report))

    def test_the_command_reads_a_record_and_prints_counts_only(self) -> None:
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            b, k, r = (Path(tmp) / n for n in ("b.json", "k.json", "decisions.jsonl"))
            b.write_text(json.dumps(backup([], entity("grey", "Greyford", kind="place"))))
            k.write_text(json.dumps(key("party")))
            r.write_text(json.dumps(said("names", "no", "Dog sleds")) + "\nnot json\n")
            with redirect_stdout(out):
                code = gkc.main([str(b), "--key", str(k), "--record", str(r), "--counts-only"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("Tiers, every key row and extra: total success", text)
        self.assertIn("flagged and turned down 1", text)
        for name in ("Greyford", "Dog sleds", "Stonehall"):
            self.assertNotIn(name, text)


class SecretNames(unittest.TestCase):
    """A name only a backstory gives (`secret_aliases`) is scored as a name of its own."""

    def setUp(self) -> None:
        self.key = key("party")
        self.key["nodes"][0]["secret_aliases"] = {"Norra Vell": ["Mira"]}

    def data(self, *who: str) -> dict[str, Any]:
        alias = {"table": "alias", "id": "al1", "entity_id": "mira", "text": "Norra Vell",
                 "key": "norra vell", "status": "confirmed"}  # fmt: skip
        rows = [{**knower(w), "id": f"k-{w}-al1", "alias_id": "al1"} for w in who]
        return backup(rows, alias)

    def scored(self, data: dict[str, Any]) -> dict[str, key_knowers.Scored]:
        return {s.id: s for s in gkc.check(data, self.key).knowers if s.section == "name"}

    def test_known_to_its_owner_alone_is_right(self) -> None:
        got = self.scored(self.data("mira"))
        self.assertEqual(got["Mira / Norra Vell"].verdict, key_knowers.RIGHT)

    def test_known_to_another_player_character_is_a_leak(self) -> None:
        got = self.scored(self.data("mira", "tavin"))
        self.assertEqual(got["Mira / Norra Vell"].verdict, key_knowers.LEAKED)

    def test_a_secret_name_the_graph_lacks_is_missing(self) -> None:
        report = gkc.check(backup([]), self.key)
        self.assertEqual(report.secret_names, {"missing": 1})
        self.assertIn("Secret names (only a backstory gives them): found 0, missing 1",
                      gkc.render(report, counts_only=True))  # fmt: skip

    def test_a_missing_entrys_secret_names_are_missing(self) -> None:
        data = backup([])
        data["sections"]["memory"] = [
            r for r in data["sections"]["memory"] if r.get("id") not in ("mira", "r1")
        ]
        self.assertEqual(gkc.check(data, self.key).secret_names, {"missing": 1})

    def test_its_name_is_never_printed_in_counts_only(self) -> None:
        text = gkc.render(gkc.check(self.data("mira", "tavin"), self.key), counts_only=True)
        self.assertNotIn("Norra", text)


if __name__ == "__main__":
    unittest.main()
