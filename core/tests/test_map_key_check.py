"""The sample map's key and its checker (#4423): pins by name, roads and "in"/"on the shore of" from
the close read, layer rows from the map, the must-not list, and what MapBot doesn't keep yet said as
such, never counted as missed. A made-up backup with the key's public place names; counts only."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.devtools import map_key_check as mkc


def _key() -> dict[str, Any]:
    return json.loads(mkc.DEFAULT_KEY.read_text(encoding="utf-8"))


def _backup(places: list[str], links: list[tuple[str, str, str]], **memory: Any) -> dict[str, Any]:
    note = {
        "by": "1", "at": 1, "alt": "A map.", "layout": "",
        "places": [{"name": n, "where": ""} for n in places],
        "close": {"level": "region", "places": [], "links": [
            {"a": a, "kind": k, "b": b, "word": "", "sure": True} for a, k, b in links
        ]},
    }  # fmt: skip
    rows = [{"table": t, **r} for t, rs in memory.items() for r in rs]
    return {
        "campaign": {"name": mkc.SAMPLE_CAMPAIGN},
        "sections": {"map_notes": [note], "memory": rows},
    }


class TheKey(unittest.TestCase):
    def test_the_key_is_readable_and_its_names_are_the_lore_keys(self) -> None:
        key = _key()
        self.assertEqual(len(key["pins"]), 22)
        lore = json.loads((mkc.DEFAULT_KEY.parent / "lore_key.json").read_text(encoding="utf-8"))
        spelled = {n["name"] for n in lore["nodes"]}
        for name in ("Easthaven", "Good Mead", "Bryn Shander", "Targos"):
            self.assertIn(name, spelled)  # (the same spelling: never a second entry)


class Check(unittest.TestCase):
    def test_what_is_kept_is_scored_and_the_rest_is_not_stored_yet(self) -> None:
        backup = _backup(
            ["Bryn Shander", "Targos", "Dougan’s Hole", "Redwaters", "Luskan", "Somewhere"],
            [("Bryn Shander", "route_to", "Targos"), ("Dougan's Hole", "next_to", "Redwaters")],
            entity=[{"id": "s", "name": "Spine of the World", "status": "confirmed"},
                    {"id": "e1", "name": "Easthaven", "status": "confirmed"},
                    {"id": "e2", "name": "Easthaven", "status": "proposed"}],
            map_fact=[
                {"entity_id": "s", "layer": "terrain", "value": "mountain", "source": "map",
                 "status": "read"},
                {"entity_id": "s", "layer": "climate", "value": "arctic", "source": "map",
                 "status": "read"},
            ],
        )  # fmt: skip
        report = mkc.check(backup, _key())
        pins = report.parts["pins (by name)"]
        self.assertEqual((pins[mkc.FOUND], pins[mkc.MISSING]), (4, 18))
        self.assertEqual(report.parts["roads"][mkc.FOUND], 1)
        self.assertEqual(report.parts["in, on the shore of"][mkc.FOUND], 1)
        self.assertEqual(report.parts["layers from the map"][mkc.FOUND], 1)
        self.assertEqual(report.extra_places, 2)  # (Luskan and Somewhere)
        self.assertEqual(
            dict(report.must_not),
            {"a climate row from the map": 1, "a place the map doesn't name": 1,
             "a second entry": 1},
        )  # fmt: skip
        lines = mkc.render(report)
        self.assertIn("distances: 8 in the key, not stored yet", lines)
        self.assertIn("pins (position): 22 in the key, not stored yet", lines)
        self.assertIn("scale: 1 in the key, not stored yet", lines)
        text = "\n".join(lines)
        for name in ("Bryn", "Targos", "Luskan", "Somewhere"):  # (counts only)
            self.assertNotIn(name, text)

    def test_in_keeps_its_way_round(self) -> None:
        links = [("Ten-Towns", "in", "Bryn Shander")]  # (the wrong way round)
        self.assertFalse(mkc._linked(
            [(mkc.name_key(a), k, mkc.name_key(b)) for a, k, b in links],
            mkc.name_key("Bryn Shander"), "in", mkc.name_key("Ten-Towns"),
        ))  # fmt: skip

    def test_only_the_sample_is_checked(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "b.json")
            path.write_text(json.dumps({"campaign": {"name": "A real campaign"}, "sections": {}}))
            self.assertEqual(mkc.main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
