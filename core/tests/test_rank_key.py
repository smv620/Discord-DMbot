"""The rank key (#3172): every entry is the master key's, each rank on the owner's levels and
each role one of the owner's codes; and its scorer says when it scores against the draft. Pure."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dmbot.devtools import rank_key_check as rk
from dmbot.devtools.graph_key_check import SAMPLE_CAMPAIGN
from dmbot.memory import roles

KEYS = Path(__file__).parent / "fixtures" / "story_graph"


class TheKey(unittest.TestCase):
    def test_entries_ranks_and_roles(self) -> None:
        key = json.loads((KEYS / "rank_key.json").read_text())
        master = json.loads((KEYS / "master_key.json").read_text())
        self.assertEqual([e["name"] for e in key["entries"]], [n["name"] for n in master["nodes"]])
        for e in key["entries"]:
            self.assertIn(e["rank"], range(6), e["name"])
            self.assertLessEqual(set(e["roles"]), set(roles.STORED_SEEDED), e["name"])
            self.assertTrue(e["note"], e["name"])

    def test_the_md_says_what_the_json_says(self) -> None:
        key = json.loads((KEYS / "rank_key.json").read_text())
        md = (KEYS / "rank_key.md").read_text()
        for e in key["entries"]:
            self.assertIn(f"| {e['name']} | {e['rank']} | {', '.join(e['roles'])} |", md)


class TheScorer(unittest.TestCase):
    def test_it_says_draft_while_the_key_is_one(self) -> None:
        self.assertIn("against the draft key", rk.render(rk.RankReport(draft=True)))
        self.assertIn("(against the key)", rk.render(rk.RankReport(draft=False)))

    def test_an_assessment_and_a_connection_are_scored(self) -> None:
        def ent(eid: str, name: str, **kw: object) -> dict[str, object]:
            return {"table": "entity", "id": eid, "name": name, "type": "character",
                    "status": "confirmed", "merged_into": None, "role": "npc", **kw}  # fmt: skip

        backup = {
            "campaign": {"name": SAMPLE_CAMPAIGN},
            "sections": {
                "memory": [
                    ent("a" * 32, "Hlin Trollbane"),
                    ent("b" * 32, "Belleros Stormgrace", role="player_character"),
                    {"table": "relation", "id": "r1", "subject_id": "a" * 32,
                     "object_id": "b" * 32, "predicate": "knows", "detail": "hostile",
                     "status": "confirmed", "to_session_at": None, "source": "dm"},
                ],
                "memory_importance": [{"entity_id": "a" * 32, "rank": 3}],
            },
        }  # fmt: skip
        key = {"status": "draft", "entries": [
            {"name": "Hlin Trollbane", "rank": 3, "roles": ["quest_giver", "antagonist"]},
            {"name": "Belleros Stormgrace", "rank": 5, "roles": []},
            {"name": "Wit", "rank": 2, "roles": []},
        ]}  # fmt: skip
        got = rk.check(backup, key, {"nodes": []})
        self.assertEqual((got.exact, got.missing), (2, ["Wit"]))
        self.assertEqual((got.roles_found, got.roles_expected), (1, 2))
        self.assertEqual(got.roles_missed, {"quest_giver": 1})


if __name__ == "__main__":
    unittest.main()
