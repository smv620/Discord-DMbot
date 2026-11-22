"""Why each answer-key name a sample run did not end up with is missing (#5055 slice A): a small
made-up backup and decision record stand in for a run, and every stage is shown once. Pure."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from dmbot.devtools import graph_key_check as gkc
from dmbot.devtools import why_missed as wm

KEY: dict[str, Any] = {
    "nodes": [
        {"name": "Tamsin Reedwell", "kind": "person", "docs": ["lore"]},
        {"name": "Brindle", "kind": "place", "docs": ["lore"]},
        {"name": "Millers' Guild", "kind": "group", "docs": ["lore"]},
        {"name": "Hollis Varn", "kind": "person", "docs": ["lore"], "aliases": ["Captain Varn"]},
        {"name": "Wenna", "kind": "person", "docs": ["lore"]},
        {"name": "Black Hand", "kind": "group", "docs": ["lore"]},
        {"name": "Reliquary of Ombra", "kind": "item", "docs": ["lore"]},
        {"name": "Thornwatch", "kind": "place", "docs": ["lore"]},
        {"name": "Old Wenna's dog", "kind": "creature", "docs": ["session"]},
        {"name": "The far shore", "kind": "place", "docs": ["lore"], "by_design": True},
    ],
    "pairs": [],
}


def entity(i: str, name: str, kind: str, status: str = "confirmed", **more: Any) -> dict[str, Any]:
    return {"table": "entity", "id": i, "name": name, "type": kind, "status": status,
            "merged_into": None, **more}  # fmt: skip


def run() -> dict[str, Any]:
    rows = [
        entity("e1", "Tamsin Reedwell", "character"),  # found and kept
        entity("e2", "Brindle", "place", "proposed"),  # waiting, the AI not sure
        entity("e3", "Millers' Guild", "faction", "rejected"),  # turned down by the AI
        entity("e4", "Varn", "character", "confirmed"),  # the key entry, under its other name
        {
            "table": "alias",
            "id": "a1",
            "entity_id": "e4",
            "text": "Captain Varn",
            "key": "captain varn",
            "status": "confirmed",
        },
        entity("e5", "Wenna", "character", "proposed", merged_into="e1"),  # joined into Tamsin
        entity("e6", "The Black Hand", "faction"),  # the article: found
        entity("e7", "Reliquary of Ombra", "place"),  # wrong kind
        entity("e8", "Gloomstone", "place", "proposed"),  # not in the key: never printed
    ]
    return {"campaign": {"name": gkc.SAMPLE_CAMPAIGN}, "sections": {"memory": rows}}


DECISIONS = {
    "e2": {"item": "e2", "decision": "not sure", "reason": "only on a map label", "done": True},
    "e3": {"item": "e3", "decision": "no", "reason": "a trade, not a group", "done": True},
}


class Explaining(unittest.TestCase):
    def whys(self) -> dict[str, wm.Why]:
        return {w.name: w for w in wm.explain(run(), KEY, "lore", DECISIONS)}

    def test_each_stage_and_its_reason(self) -> None:
        got = self.whys()
        self.assertEqual(got["Brindle"].stage, wm.WAITING)
        self.assertIn("not sure: only on a map label", got["Brindle"].reason)
        self.assertEqual(got["Millers' Guild"].stage, wm.DECIDED_NO)
        self.assertIn("said no: a trade, not a group", got["Millers' Guild"].reason)
        self.assertEqual(got["Wenna"].stage, wm.MERGED)
        self.assertIn('"Tamsin Reedwell"', got["Wenna"].reason)
        self.assertEqual(got["Reliquary of Ombra"].stage, wm.WRONG_KIND)
        self.assertIn("place", got["Reliquary of Ombra"].reason)
        self.assertEqual(got["Thornwatch"].stage, wm.NOT_STORED)

    def test_found_by_design_and_other_documents_are_left_out(self) -> None:
        got = self.whys()
        for name in (
            "Tamsin Reedwell",
            "Hollis Varn",
            "Black Hand",
            "The far shore",
            "Old Wenna's dog",
        ):
            self.assertNotIn(name, got)

    def test_never_asked_and_a_refused_yes(self) -> None:
        got = {w.name: w for w in wm.explain(run(), KEY, "lore", {})}
        self.assertIn("never asked", got["Brindle"].reason)
        self.assertIn("no decision on record", got["Millers' Guild"].reason)
        refused = {"e2": {"item": "e2", "decision": "yes", "reason": "a town", "done": False}}
        got = {w.name: w for w in wm.explain(run(), KEY, "lore", refused)}
        self.assertIn("a rule refused it", got["Brindle"].reason)

    def test_a_long_reason_is_cut(self) -> None:
        long = {"e2": {"item": "e2", "decision": "not sure", "reason": "x " * 200, "done": True}}
        (w,) = [w for w in wm.explain(run(), KEY, "lore", long) if w.name == "Brindle"]
        self.assertLessEqual(len(w.reason), len("the careful AI was not sure: ") + wm.REASON_MAX)

    def test_the_report_names_key_entries_only(self) -> None:
        text = wm.render(wm.explain(run(), KEY, "lore", DECISIONS))
        self.assertIn(
            "Why missed: not stored 1, decided no 1, merged 1, wrong kind 1, waiting 1", text
        )
        self.assertNotIn("Gloomstone", text)
        self.assertNotIn("Varn,", text)

    def test_the_merged_into_entry_is_named_only_when_the_key_lists_it(self) -> None:
        data = run()
        data["sections"]["memory"][4 + 1]["merged_into"] = "e8"  # (Wenna into Gloomstone)
        got = {w.name: w for w in wm.explain(data, KEY, "lore", DECISIONS)}
        self.assertIn("an entry the key doesn't list", got["Wenna"].reason)


class TheArticle(unittest.TestCase):
    def test_a_stored_name_with_an_article_is_the_key_s_name_without(self) -> None:
        status = {r.text: r.status for r in gkc.check(run(), KEY, "lore").entries}
        self.assertEqual(status["Black Hand"], gkc.FOUND)


class TheCommand(unittest.TestCase):
    def test_why_missed_with_the_decision_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, key, rec = Path(tmp) / "b.json", Path(tmp) / "k.json", Path(tmp) / "d.jsonl"
            path.write_text(json.dumps(run()))
            key.write_text(json.dumps(KEY))
            rec.write_text("".join(json.dumps(r) + "\n" for r in DECISIONS.values()) + "not json\n")
            out = io.StringIO()
            with redirect_stdout(out):
                code = gkc.main(
                    [str(path), "--key", str(key), "--docs", "lore", "--why-missed",
                     "--decisions", str(rec)]
                )  # fmt: skip
            self.assertEqual(code, 0)
            self.assertIn(
                "- Millers' Guild (group): decided no: the careful AI said no", out.getvalue()
            )
            out = io.StringIO()
            with redirect_stdout(out):
                gkc.main(
                    [
                        str(path),
                        "--key",
                        str(key),
                        "--docs",
                        "lore",
                        "--why-missed",
                        "--counts-only",
                    ]
                )
            self.assertIn("Why missed: not stored 1", out.getvalue())
            self.assertNotIn("Millers", out.getvalue())


if __name__ == "__main__":
    unittest.main()
