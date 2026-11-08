"""The story roles a connection shows (#3172, item b): each rule of the agreed table, the guards
on direction and on player characters, ended and unconfirmed connections, and merged names.
Pure; invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.roles_backed import backed_roles

PC, NPC = "player_character", "npc"


def ent(kind: str = "character", role: str | None = NPC, **kw: Any) -> dict[str, Any]:
    return {"type": kind, "role": role, "status": "confirmed", "merged_into": None, **kw}


ENTITIES: dict[str, dict[str, Any]] = {
    "tavin": ent(role=PC), "liane": ent(role=PC),
    "hara": ent(), "vex": ent(), "snik": ent(), "pip": ent(), "bessa": ent(), "corin": ent(),
    "lanterns": ent("faction", None), "aldmoor": ent("place", None),
    "quest": ent("thread", None), "chest": ent("item", None),
}  # fmt: skip


def rel(a: str, how: str, b: str, detail: str = "", **kw: Any) -> dict[str, Any]:
    return {"id": f"{a}-{how}-{b}", "subject_id": a, "predicate": how, "object_id": b,
            "detail": detail, "status": "confirmed", "to_session_at": None, **kw}  # fmt: skip


def roles_of(*rels: dict[str, Any], entities: dict[str, Any] = ENTITIES) -> dict[str, set[str]]:
    return {e: set(held) for e, held in backed_roles(entities, rels).items()}


class EachRule(unittest.TestCase):
    def test_quest_parts_named_in_the_thread_words(self) -> None:
        got = roles_of(
            rel("bessa", "in_thread", "quest", "Quest giver"),
            rel("corin", "in_thread", "quest", "the target"),
            rel("hara", "in_thread", "quest", "a victim"),
        )
        self.assertEqual(got, {"bessa": {"quest_giver"}, "corin": {"quest_target"}})

    def test_a_mentor_or_rival_of_a_player_character_or_a_companion(self) -> None:
        got = roles_of(
            rel("hara", "mentor_of", "tavin"),
            rel("pip", "travels_with", "liane"),
            rel("corin", "rival_of", "pip"),  # (a companion's rival counts)
            rel("liane", "rival_of", "vex"),  # (either way round)
            rel("bessa", "mentor_of", "snik"),  # (an NPC's mentor says little)
        )
        self.assertEqual(got["hara"], {"mentor"})
        self.assertEqual(got["corin"], {"rival"})
        self.assertEqual(got["vex"], {"rival"})
        self.assertEqual(got["pip"], {"companion"})
        self.assertNotIn("bessa", got)

    def test_an_antagonist_is_hostile_toward_a_pc_and_never_because_a_pc_hates_them(
        self,
    ) -> None:
        got = roles_of(
            rel("vex", "knows", "tavin", "sworn enemy"),
            rel("liane", "knows", "corin", "hostile"),  # (the PC's feeling: no role for Corin)
            rel("bessa", "enemy_of", "liane"),  # (an old row)
        )
        self.assertEqual(got, {"vex": {"antagonist"}, "bessa": {"antagonist"}})

    def test_a_minion_serves_an_antagonist(self) -> None:
        got = roles_of(
            rel("vex", "knows", "tavin", "hostile"),
            rel("snik", "serves", "vex"),
            rel("pip", "serves", "hara"),  # (Hara is no antagonist)
        )
        self.assertEqual(got["snik"], {"minion"})
        self.assertNotIn("pip", got)

    def test_a_ruler_leads_a_group_or_a_place_and_an_ally_is_friendly_to_a_pc(self) -> None:
        got = roles_of(
            rel("hara", "leads", "lanterns"),
            rel("bessa", "leads", "aldmoor"),
            rel("corin", "knows", "tavin", "trusts"),
            rel("snik", "knows", "tavin", "wary"),  # (wary is no ally)
            rel("liane", "ally_of", "vex"),
            rel("corin", "captive_of", "vex"),
        )
        self.assertEqual(got["hara"], {"ruler"})
        self.assertEqual(got["bessa"], {"ruler"})
        self.assertEqual(got["corin"], {"ally", "captive"})
        self.assertEqual(got["vex"], {"ally"})
        self.assertNotIn("snik", got)


class WhatDoesNotCount(unittest.TestCase):
    def test_a_player_character_holds_no_backed_role(self) -> None:
        got = roles_of(rel("tavin", "leads", "lanterns"), rel("tavin", "mentor_of", "liane"))
        self.assertEqual(got, {})

    def test_an_ended_or_unconfirmed_connection_backs_nothing(self) -> None:
        got = roles_of(
            rel("hara", "mentor_of", "tavin", to_session_at=100),
            rel("vex", "knows", "tavin", "hostile", status="proposed"),
        )
        self.assertEqual(got, {})

    def test_a_merged_name_s_connections_back_the_entry_it_became(self) -> None:
        entities = {**ENTITIES, "old_hara": ent(status="merged", merged_into="hara")}
        got = roles_of(rel("old_hara", "mentor_of", "tavin"), entities=entities)
        self.assertEqual(got, {"hara": {"mentor"}})

    def test_the_connection_ids_are_kept_to_open_with_their_lines(self) -> None:
        got = backed_roles(ENTITIES, [rel("hara", "mentor_of", "tavin")])
        self.assertEqual(got["hara"]["mentor"], ["hara-mentor_of-tavin"])


if __name__ == "__main__":
    unittest.main()
