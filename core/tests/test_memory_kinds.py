"""One kind for each memory entry (#1034, owner decision 2026-10-10), without a database:
the kinds, roles and links to the rules in plain words, the block list, the older kinds in
older backups, and Find names with spells and species left out."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import backup
from dmbot.memory.kinds import LEGACY_TAG, NOT_IN_RULES, kind_key, kind_phrase, links_text
from dmbot.memory.models import CONFIRMED, Entity, MemoryRuleError, RuleLink
from dmbot.memory.name_documents import instructions
from dmbot.memory.name_list import parse
from dmbot.memory.ontology import BLOCKED_KINDS, NOT_A_KIND, SPELL_NOT_KEPT, Ontology, TypeTerm
from dmbot.memory.scan import find_new_names

E1, E2, E3 = "1" * 32, "2" * 32, "3" * 32


def entity(
    kind: str = "character",
    role: str | None = None,
    *,
    name: str = "Snot",
    needs_look: bool = False,
    played_by: int | None = None,
) -> Entity:
    return Entity(E1, kind, name, "", CONFIRMED, None, "dm", 0, played_by, role, needs_look)


def link(
    kind: str,
    name: str,
    *,
    rules_source: str = "srd52",
    edition: str | None = None,
    known: bool = True,
    n: int = 0,
) -> RuleLink:
    return RuleLink(f"{n:032x}", E1, kind, rules_source if known else "", name, edition, known, n)


class Phrases(unittest.TestCase):
    def test_snot_the_goblin_prince(self) -> None:
        links = [
            link("stat_block", "Goblin Warrior"),
            link("creature_type", "humanoid", known=True, n=1),
            link("species", "goblin", n=2),
        ]
        self.assertEqual(
            kind_phrase(entity(role="npc"), links),
            "character · NPC · goblin (humanoid) · Goblin Warrior",
        )

    def test_auril_is_a_character_with_the_role_god(self) -> None:
        self.assertEqual(kind_phrase(entity(role="god", name="Auril")), "character · god")

    def test_the_other_kinds_have_no_role_or_links(self) -> None:
        self.assertEqual(kind_phrase(entity("place")), "place")
        self.assertEqual(kind_phrase(entity("faction")), "group")
        self.assertEqual(kind_phrase(entity("concept")), "idea")
        self.assertEqual(kind_phrase(entity("item")), "item")
        self.assertEqual(kind_phrase(entity("event")), "event")

    def test_a_player_character_and_a_character_with_no_role_yet(self) -> None:
        self.assertEqual(
            kind_phrase(entity(role="player_character", played_by=7)),
            "character · player character",
        )
        self.assertEqual(kind_phrase(entity()), "character")

    def test_species_is_shown_only_as_the_2014_name_tagged_legacy(self) -> None:
        text = links_text([link("species", "half-orc", edition="2014")])
        self.assertEqual(text, [f"half-orc {LEGACY_TAG}"])

    def test_a_name_not_in_the_rules_keeps_its_words_and_says_so(self) -> None:
        text = links_text([link("species", "Gnoll prince", known=False)])
        self.assertEqual(text, [f"Gnoll prince ({NOT_IN_RULES})"])

    def test_a_creature_type_alone_and_several_classes(self) -> None:
        self.assertEqual(links_text([link("creature_type", "undead")]), ["undead"])
        classes = [link("class", "wizard", n=1), link("class", "rogue", n=2)]
        self.assertEqual(links_text(classes), ["wizard", "rogue"])
        order = [link("background", "sage"), link("class", "wizard", n=1)]
        self.assertEqual(links_text(order), ["wizard", "sage"])  # a fixed order

    def test_an_entry_from_an_older_kind_says_it_needs_a_look(self) -> None:
        self.assertEqual(kind_phrase(entity(needs_look=True)), "character · needs a look")
        self.assertEqual(kind_phrase(entity("concept", needs_look=True)), "idea · needs a look")

    def test_the_words_are_plain(self) -> None:
        text = kind_phrase(entity(role="npc"), [link("species", "elf")])
        for word in ("ontology", "entity", "predicate", "stat_block", "creature_type"):
            self.assertNotIn(word, text)

    def test_the_key_the_lists_use(self) -> None:
        self.assertEqual(kind_key("character", "npc"), "npc")
        self.assertEqual(kind_key("character", "player_character"), "player_character")
        self.assertEqual(kind_key("character", "god"), "deity")
        self.assertEqual(kind_key("character", None), "character")
        self.assertEqual(kind_key("place", None), "place")
        self.assertEqual(entity(role="god").kind_key, "deity")
        self.assertEqual(entity("place").kind_key, "place")
        self.assertTrue(entity(role="player_character").is_player_character)
        self.assertFalse(entity(role="npc").is_player_character)


class BlockList(unittest.TestCase):
    def test_a_role_or_a_rules_category_is_not_a_kind(self) -> None:
        onto = Ontology.build()
        for word in ("NPC", "player character", "God", "monster", "Goblin", "wizard", "Undead",
                     "species", "stat_block", "creature type", "feat", "half-elf"):  # fmt: skip
            with self.subTest(word), self.assertRaises(MemoryRuleError) as caught:
                onto.check_new_type(
                    TypeTerm(word.lower().replace(" ", "_"), "character", word, "d")
                )
            self.assertEqual(str(caught.exception), NOT_A_KIND.format(label=word))

    def test_the_message_is_plain_and_says_what_to_do(self) -> None:
        text = NOT_A_KIND.format(label="goblin")
        self.assertIn("role", text)
        self.assertIn("link it to the rules", text)
        for word in ("ontology", "entity", "predicate", "taxonomy"):
            self.assertNotIn(word, text)

    def test_a_real_kind_still_gets_through(self) -> None:
        onto = Ontology.build()
        for key, label in (("ship", "ship"), ("horse", "named horse"), ("dungeon", "dungeon")):
            onto.check_new_type(TypeTerm(key, "item", label, "d"))  # no error

    def test_the_list_holds_the_roles_and_the_rules_words(self) -> None:
        for word in ("npc", "god", "deity", "monster", "creature", "spell", "class", "species"):
            self.assertIn(word, BLOCKED_KINDS)


def _row(table: str, **values: Any) -> dict[str, Any]:
    return {"table": table, **values}


def _old_entity(eid: str, kind: str, name: str, played_by: int | None = None) -> dict[str, Any]:
    return _row(
        "entity", id=eid, type=kind, name=name, description="", status="confirmed",
        merged_into=None, source="dm", created_at=1, played_by=played_by,
    )  # fmt: skip


class OlderBackups(unittest.TestCase):
    def test_the_older_kinds_are_mapped_as_the_migration_does(self) -> None:
        rows = [
            _old_entity("a" * 32, "npc", "Snot"),
            _old_entity("b" * 32, "player_character", "Testa", played_by=5),
            _old_entity("c" * 32, "deity", "Auril"),
            _old_entity("d" * 32, "creature", "Rex"),
            _old_entity("e" * 32, "spell", "Fireball"),
            _old_entity("f" * 32, "place", "Bryn Shander"),
        ]
        got = {r["name"]: r for r in backup._from_before_kinds(rows)}
        self.assertEqual(
            {n: (r["type"], r["role"], r["needs_look"]) for n, r in got.items()},
            {
                "Snot": ("character", "npc", False),
                "Testa": ("character", "player_character", False),
                "Auril": ("character", "god", False),
                "Rex": ("character", None, True),  # a named creature: the DM decides
                "Fireball": ("concept", None, True),  # a spell becomes an idea to look at
                "Bryn Shander": ("place", None, False),
            },
        )
        self.assertEqual(got["Testa"]["played_by"], 5)  # still played by them

    def test_nothing_is_dropped(self) -> None:
        rows = [_old_entity(f"{n:032x}", "creature", f"C{n}") for n in range(5)]
        self.assertEqual(len(backup._from_before_kinds(rows)), 5)

    def test_a_campaigns_own_kinds_and_relationships_follow(self) -> None:
        rows = [
            _old_entity("a" * 32, "npc", "Snot"),
            _row("type", key="villain", parent="npc", label="villain"),
            _row("type", key="ship", parent="item", label="ship"),
            _row(
                "predicate", key="friends_with", subject_types=["npc", "player_character"],
                object_types=["creature", "faction", "spell"],
            ),
        ]  # fmt: skip
        got = backup._from_before_kinds(rows)
        self.assertEqual(got[1]["parent"], "character")
        self.assertEqual(got[2]["parent"], "item")
        self.assertEqual(got[3]["subject_types"], ["character"])  # one, not two
        self.assertEqual(got[3]["object_types"], ["character", "faction", "concept"])

    def test_a_current_backup_passes_through_as_it_is(self) -> None:
        rows = [
            {**_old_entity("a" * 32, "character", "Snot"), "role": "npc", "needs_look": False},
            {**_old_entity("b" * 32, "place", "Tower"), "role": None, "needs_look": False},
        ]
        self.assertIs(backup._from_before_kinds(rows), rows)

    def test_a_file_with_no_entries_is_left_alone(self) -> None:
        rows = [_row("alias", id="a" * 32)]
        self.assertIs(backup._from_before_kinds(rows), rows)


def _current(eid: str, kind: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        **_old_entity(eid, kind, name, extra.pop("played_by", None)),
        "role": extra.pop("role", None),
        "needs_look": extra.pop("needs_look", False),
        **extra,
    }


def _link_row(eid: str, entity_id: str, kind: str = "species", **extra: Any) -> dict[str, Any]:
    return {
        "table": "rule_link", "id": eid, "entity_id": entity_id, "kind": kind,
        "rules_source": "srd52", "name": "goblin", "edition": None, "known": True,
        "created_at": 1, **extra,
    }  # fmt: skip


class CheckedBackups(unittest.TestCase):
    def check(self, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        return backup._checked_rows(rows)

    def test_roles_and_links_are_accepted(self) -> None:
        got = self.check(
            [
                _current(E1, "character", "Snot", role="npc"),
                _current(E2, "character", "Testa", role="player_character", played_by=5),
                _link_row("4" * 32, E1),
                _link_row("5" * 32, E1, "creature_type", name="humanoid"),
            ]
        )
        self.assertEqual(len(got["rule_link"]), 2)
        self.assertEqual(len(got["entity"]), 2)

    def test_an_older_file_is_read_and_checked(self) -> None:
        got = self.check([_old_entity(E1, "npc", "Snot"), _old_entity(E2, "spell", "Fireball")])
        kinds = [(r["type"], r["role"]) for r in got["entity"]]
        self.assertEqual(kinds, [("character", "npc"), ("concept", None)])

    def test_damage_is_refused(self) -> None:
        bad: list[list[dict[str, Any]]] = [
            [_current(E1, "place", "Tower", role="npc")],  # only a character has a role
            [_current(E1, "character", "Snot", role="demigod")],
            [_current(E1, "character", "Snot", role="npc", played_by=5)],  # only a PC is played
            [_current(E1, "character", "Snot", role="npc"), _link_row("4" * 32, E3)],  # no entry
            [_current(E1, "place", "Tower"), _link_row("4" * 32, E1)],  # not a character
            [_current(E1, "character", "Snot"), _link_row("4" * 32, E1, "alignment")],
            [_current(E1, "character", "Snot"), _link_row("4" * 32, E1, edition="1999")],
            [_current(E1, "character", "Snot"), _link_row("4" * 32, E1, known=False)],  # has source
            [_current(E1, "character", "Snot"), _link_row("4" * 32, E1, name="x" * 101)],
            [_current(E1, "character", "Snot"), _link_row("4" * 32, E1, rules_source="s" * 41)],
            [_current(E1, "character", "Snot", needs_look="yes")],
            [_current(E1, "character", "Snot"), _link_row("not-an-id", E1)],
        ]
        for rows in bad:
            with self.subTest(rows[-1]), self.assertRaises(CampaignError):
                self.check(rows)

    def test_a_link_to_an_unknown_name_keeps_its_words(self) -> None:
        rows = [
            _current(E1, "character", "Snot"),
            _link_row("4" * 32, E1, name="Gnoll prince", rules_source="", known=False),
        ]
        self.assertEqual(self.check(rows)["rule_link"][0]["name"], "Gnoll prince")


class FindNames(unittest.TestCase):
    def test_a_spell_line_is_refused_with_the_way_to_look_it_up(self) -> None:
        parsed = parse("Fireball | spell\nSnot | NPC\nMagic Missile | magic\n", secrets=False)
        self.assertEqual([line.name for line in parsed.lines], ["Snot"])
        self.assertEqual(parsed.refused, [(1, SPELL_NOT_KEPT), (3, SPELL_NOT_KEPT)])
        self.assertIn("/dmbot rule", SPELL_NOT_KEPT)

    def test_the_role_words_read_as_the_older_keys_the_store_knows(self) -> None:
        text = "Snot | NPC\nTesta | pc\nAuril | god\nRex | monster\nTower | place\nThing\n"
        kinds = {line.name: line.kind for line in parse(text, secrets=False).lines}
        self.assertEqual(
            kinds,
            {"Snot": "npc", "Testa": "player_character", "Auril": "deity", "Rex": "creature",
             "Tower": "place", "Thing": None},
        )  # fmt: skip

    def test_the_ai_is_told_to_leave_out_spells_species_and_kinds_of_creature(self) -> None:
        text = instructions(secrets=False)
        for phrase in ("Leave out spells", "species and kinds of creature", "goblins"):
            self.assertIn(phrase, text)
        self.assertNotIn("god, spell", text)  # no spell among the kinds it may answer with

    def test_three_goblins_make_no_entry(self) -> None:
        lines = ["Three goblins attack the cart.", "The goblins run away.", "Two goblins flee."]
        self.assertEqual(find_new_names(lines), [])


if __name__ == "__main__":
    unittest.main()
