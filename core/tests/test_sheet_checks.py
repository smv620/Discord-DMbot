"""Character sheets, part D1 (#1124): the wider allow list and the import status card.
The character is made up (placeholder names and numbers), never a real one or sourcebook
text; the fixture is built here in D&D Beyond's shape."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from dmbot.memory import sheet_checks as checks
from dmbot.memory import sheet_names, sheets


def mod(kind: str, sub: str, **more: Any) -> dict[str, Any]:
    return {"type": kind, "subType": sub, **more}


def belleros() -> dict[str, Any]:
    """Ranger 3 + Druid 3, 11 / 26 + 0 hit points, AC 18, with every field filled."""
    return {
        "success": True,
        "data": {
            "name": "Belleros",
            "notes": {"backstory": "LEAK: private"},
            "race": {
                "fullName": "Wood Elf",
                "weightSpeeds": {"normal": {"walk": 35}},
                "racialTraits": [{"definition": {"name": "Keen Senses"}}],
            },
            "background": {"definition": {"name": "Outlander", "description": "LEAK: text"}},
            "classes": [
                {"level": 3, "definition": {"name": "Ranger"}, "classFeatures": [
                    {"definition": {"name": "Favored Enemy"}}]},
                {"level": 3, "definition": {"name": "Druid"}, "classFeatures": [
                    {"definition": {"name": "Wild Shape"}}]},
            ],
            "stats": [{"id": i + 1, "value": v} for i, v in enumerate([10, 16, 14, 10, 14, 8])],
            "baseHitPoints": 14,
            "removedHitPoints": 15,
            "temporaryHitPoints": 0,
            "conditions": [{"id": 11}, {"id": 4, "level": 2}],
            "feats": [{"definition": {"name": "Alert"}}, {"definition": {"name": "Sharpshooter"}}],
            "classSpells": [{"spells": [
                {"definition": {"name": "Fireball"}},
                {"definition": {"name": "Cure Wounds"}},
                {"definition": {"name": "Absorb Elements"}},  # a book spell, not in the SRD
            ]}],
            "modifiers": {
                "race": [mod("set-base", "darkvision", value=60),
                         mod("language", "common", friendlySubtypeName="Common"),
                         mod("language", "elvish", friendlySubtypeName="Elvish")],
                "class": [
                    mod("proficiency", "strength-saving-throws"),
                    mod("proficiency", "dexterity-saving-throws"),
                    mod("proficiency", "survival"),
                    mod("proficiency", "perception"),
                    mod("expertise", "perception"),
                    mod("proficiency", "light-armor", friendlySubtypeName="Light Armor"),
                    mod("proficiency", "shields", friendlySubtypeName="Shields"),
                    mod("proficiency", "simple-weapons", friendlySubtypeName="Simple Weapons"),
                    mod("proficiency", "herbalism-kit", friendlySubtypeName="Herbalism Kit"),
                    mod("resistance", "fire", friendlySubtypeName="fire"),
                    mod("immunity", "poisoned", friendlySubtypeName="poisoned"),
                    mod("vulnerability", "cold", friendlySubtypeName="cold"),
                ],
                "item": [mod("bonus", "armor-class", value=1)],
            },
            "inventory": [
                {"equipped": True, "quantity": 1, "definition": {
                    "name": "Studded Leather", "armorClass": 12, "armorTypeId": 1,
                    "weight": 13, "rarity": "Common"}},
                {"equipped": True, "quantity": 1, "definition": {
                    "name": "Shield", "armorClass": 2, "armorTypeId": 4, "weight": 6,
                    "rarity": "Common"}},
                {"quantity": 20, "definition": {
                    "name": "Arrows", "weight": 1, "bundleSize": 20, "rarity": "Common"}},
                {"quantity": 1, "definition": {"name": "Ring of Mind Shielding",
                                               "weight": 0, "rarity": "Uncommon"}},
                {"quantity": 1, "definition": {"name": "Cloak of Displacement",
                                               "weight": 1, "rarity": "Rare"}},
            ],
        },
    }  # fmt: skip


class Fields(unittest.TestCase):
    def setUp(self) -> None:
        self.s = sheets.parse(belleros())

    def test_the_basic_fields(self) -> None:
        s = self.s
        self.assertEqual((s["name"], s["species"]), ("Belleros", "Wood Elf"))
        self.assertEqual(
            [(c["name"], c["level"]) for c in s["classes"]], [("Ranger", 3), ("Druid", 3)]
        )
        # 14 base + constitution +2 a level for 6 levels = 26; 15 damage taken leaves 11.
        self.assertEqual((s["hp"], s["max_hp"], s["temp_hp"]), (11, 26, 0))
        # studded leather 12 + dexterity +3, a shield +2, a +1 bonus
        self.assertEqual(s["ac"], 18)

    def test_the_full_fields(self) -> None:
        s = self.s
        self.assertEqual(
            s["abilities"], {"str": 10, "dex": 16, "con": 14, "int": 10, "wis": 14, "cha": 8}
        )
        self.assertEqual((s["conditions"], s["exhaustion"]), (["Exhaustion", "Poisoned"], 2))
        self.assertEqual(s["resistances"], ["Fire"])
        self.assertEqual(s["immunities"], ["Poisoned"])
        self.assertEqual(s["vulnerabilities"], ["Cold"])
        self.assertEqual(
            (s["skills"], s["expertise"]), (["perception", "survival"], ["perception"])
        )
        self.assertEqual(s["armor_profs"], ["Light Armor", "Shields"])
        self.assertEqual(s["weapon_profs"], ["Simple Weapons"])
        self.assertEqual(s["tools"], ["Herbalism Kit"])
        self.assertEqual(s["feats"], ["Alert", "Sharpshooter"])
        self.assertEqual(s["features"], ["Keen Senses", "Favored Enemy", "Wild Shape"])
        self.assertEqual((s["background"], s["speed"]), ("Outlander", 35))
        self.assertEqual(s["senses"], {"darkvision": 60})
        self.assertEqual(s["languages"], ["Common", "Elvish"])

    def test_the_inventory_counts_lines_weight_and_rarity(self) -> None:
        # 13 + 6 + (20 arrows in bundles of 20 at 1 lb) + 0 + 1
        self.assertEqual(
            self.s["inventory"],
            {
                "count": 5,
                "weight": 21.0,
                "rarity": {"common": 3, "uncommon": 1, "rare": 1, "very rare": 0,
                           "legendary": 0, "artifact": 0},
            },
        )  # fmt: skip

    def test_nothing_but_names_and_numbers_is_kept(self) -> None:
        self.assertNotIn("LEAK", json.dumps(self.s))

    def test_a_kept_snapshot_survives_the_allow_list_unchanged(self) -> None:
        self.assertEqual(sheets.clean(self.s), self.s)
        self.assertEqual(sheets.clean(json.loads(json.dumps(self.s))), self.s)

    def test_a_snapshot_from_before_has_the_new_fields_empty_not_wrong(self) -> None:
        old = {k: v for k, v in self.s.items() if k in {
            "v", "source", "name", "species", "classes", "level", "abilities", "max_hp",
            "ac", "speed", "proficiency_bonus", "saves", "skills", "senses", "languages",
            "spells", "features", "feats", "items"}}  # fmt: skip
        cleaned = sheets.clean(old)
        assert cleaned is not None
        self.assertEqual(
            (cleaned["hp"], cleaned["inventory"], cleaned["conditions"]), (None, None, [])
        )

    def test_the_size_cap_trims_the_new_lists_too(self) -> None:
        data = belleros()
        data["data"]["modifiers"]["class"] += [
            mod(
                "proficiency",
                f"thing-{i}-tools",
                friendlySubtypeName=f"Tool Number {i} " + "x" * 40,
            )
            for i in range(300)
        ]
        snap = sheets.parse(data)
        self.assertLessEqual(
            len(json.dumps(snap, ensure_ascii=False).encode()), sheets.SNAPSHOT_MAX_BYTES
        )
        self.assertLessEqual(len(snap["tools"]), sheets.LIST_MAX)

    def test_bad_numbers_are_flagged_for_the_card(self) -> None:
        data = belleros()
        data["data"]["removedHitPoints"] = -5
        data["data"]["stats"][0]["value"] = -3
        data["data"]["inventory"][0]["quantity"] = -1
        self.assertEqual(sheets.parse(data)["flags"], ["abilities", "basic", "inventory"])
        self.assertEqual(self.s["flags"], [])


class Statuses(unittest.TestCase):
    def setUp(self) -> None:
        self.s = sheets.parse(belleros())

    def by_part(self, snapshot: dict[str, Any]) -> dict[str, checks.Check]:
        return {c.part: c for c in checks.checks(snapshot)}

    def test_the_owners_example(self) -> None:
        basic = self.by_part(self.s)["basic"]
        self.assertEqual(
            basic.line.split(" (")[0],
            "✅ Character: Belleros, Ranger 3 + Druid 3, HP 11 / 26 + 0, AC 18",
        )

    def test_a_clean_sheet_has_a_warning_only_for_names_outside_the_free_rules(self) -> None:
        parts = self.by_part(self.s)
        self.assertEqual(parts["spells"].status, checks.WARN)  # Absorb Elements
        self.assertIn("'Absorb Elements'", parts["spells"].text)
        self.assertIn("2 others fine", parts["spells"].text)
        self.assertEqual(parts["feats"].status, checks.WARN)  # Sharpshooter: a book feat
        self.assertEqual(
            {p: c.status for p, c in parts.items() if p not in ("spells", "feats", "basic")},
            {p: checks.OK for p in parts if p not in ("spells", "feats", "basic")},
        )
        self.assertEqual(
            parts["inventory"].line,
            "✅ Inventory (5 items, 21.00 lb): 1 rare, 1 uncommon, 3 common",
        )

    def test_the_inventory_line_matches_the_issues_example(self) -> None:
        snap = copy.deepcopy(self.s)
        snap["inventory"] = {"count": 18, "weight": 111.58,
                             "rarity": {"common": 14, "uncommon": 3, "rare": 1, "very rare": 0,
                                        "legendary": 0, "artifact": 0}}  # fmt: skip
        self.assertEqual(
            self.by_part(snap)["inventory"].line,
            "✅ Inventory (18 items, 111.58 lb): 1 rare, 3 uncommon, 14 common",
        )

    def test_levels_over_twenty_do_not_add_up(self) -> None:
        snap = copy.deepcopy(self.s)
        snap["classes"] = [{"name": "Ranger", "level": 15, "subclass": None},
                           {"name": "Druid", "level": 9, "subclass": None}]  # fmt: skip
        basic = self.by_part(snap)["basic"]
        self.assertEqual(basic.status, checks.BAD)
        self.assertIn("class levels add up to 24", basic.text)

    def test_hit_points_over_max_plus_temporary_do_not_add_up(self) -> None:
        snap = {**self.s, "hp": 40, "temp_hp": 3}
        basic = self.by_part(snap)["basic"]
        self.assertEqual(basic.status, checks.BAD)
        self.assertIn("HP 40 is above the maximum 26 + 3", basic.text)
        self.assertEqual(
            self.by_part({**self.s, "hp": 29, "temp_hp": 3})["basic"].status, checks.OK
        )

    def test_a_negative_number_is_not_ok(self) -> None:
        data = belleros()
        data["data"]["stats"][0]["value"] = -3
        parts = self.by_part(sheets.parse(data))
        self.assertEqual(parts["abilities"].status, checks.BAD)

    def test_unreadable_parts_are_not_ok(self) -> None:
        snap = {**self.s, "abilities": {}, "inventory": None, "max_hp": None, "ac": None}
        parts = self.by_part(snap)
        self.assertEqual(
            [parts[p].status for p in ("abilities", "inventory", "basic")], [checks.BAD] * 3
        )

    def test_expertise_without_proficiency_does_not_add_up(self) -> None:
        snap = {**self.s, "skills": ["survival"], "expertise": ["perception"]}
        self.assertEqual(self.by_part(snap)["skills"].status, checks.BAD)

    def test_rarity_counts_must_add_up_to_the_items(self) -> None:
        inv = {**self.s["inventory"], "count": 9}
        self.assertEqual(self.by_part({**self.s, "inventory": inv})["inventory"].status, checks.BAD)

    def test_a_typed_name_nothing_like_any_feat_is_an_error_and_a_near_one_is_a_warning(
        self,
    ) -> None:
        typed = {
            **self.s,
            "source": "typed",
            "feats": ["Alert", "Clowning", "Sharpshoter", "Lucky", "Tough"],
        }
        feats = {c.part: c for c in checks.checks({**typed, "source": "typed"})}
        # a typed sheet only has the basics and features; check the feats rule directly
        self.assertNotIn("feats", feats)
        full = {c.part: c for c in checks.checks({**typed, "source": "dndbeyond"})}
        self.assertEqual(full["feats"].status, checks.WARN)  # D&D Beyond's own data: never an error
        doc = checks._named(
            "feats",
            "Feats",
            typed["feats"],
            lambda n: checks._kind_of({"source": "document"}, "feat", n)[0],
        )
        self.assertEqual(doc.status, checks.BAD)
        self.assertEqual(
            doc.line,
            "⛔ Feats: 'Clowning' not recognized; 4 others checked. "
            "Check the spelling on your sheet.",
        )
        near = checks._named(
            "feats",
            "Feats",
            ["Sharpshoter"],
            lambda n: checks._kind_of({"source": "document"}, "feat", n)[0],
        )
        self.assertEqual(near.status, checks.WARN)

    def test_a_typed_sheet_is_only_checked_for_what_the_form_asks(self) -> None:
        typed = sheets.typed("Mira", species="Elf", class_name="Wizard", level=3, names=["Shield"])
        assert typed is not None
        self.assertEqual([c.part for c in checks.checks(typed)], ["basic", "features"])

    def test_names_are_looked_up_like_the_rules_index(self) -> None:
        self.assertEqual(sheet_names.where("class", "ranger"), "srd")
        self.assertEqual(sheet_names.where("class", "Artificer"), "official")
        self.assertEqual(sheet_names.where("species", "Hill Dwarf"), "srd")
        self.assertEqual(sheet_names.where("class", "Clown"), "unknown")
        self.assertEqual(sheet_names.close("feat", "Sharpshoter"), "Sharpshooter")


class Card(unittest.TestCase):
    def test_the_card_is_a_summary_one_line_per_part_and_one_message(self) -> None:
        text = checks.card(sheets.parse(belleros()))
        lines = text.split("\n")
        self.assertIn("Belleros", lines[0])
        self.assertIn("2 names to glance at", lines[0])  # the feat and the spell list
        self.assertIn("Only you see this", lines[0])
        parts = [ln for ln in lines[1:] if ln[:1] in "✅⚠⛔" and not ln.startswith("⚠️ is not")]
        self.assertEqual(len(parts), 11)
        self.assertEqual([ln[0] for ln in parts[:2]], ["⚠", "⚠"])  # the ones to look at first
        self.assertLess(len(text), 2000)
        self.assertTrue(lines[-1].startswith("⚠️ is not a problem."))

    def test_a_clean_sheet_says_what_it_does_not_know(self) -> None:
        snap = {**sheets.parse(belleros()), "spells": ["Fireball"], "feats": ["Alert"]}
        snap["classes"] = [{"name": "Ranger", "level": 3, "subclass": None}]
        snap["species"] = "Elf"
        text = checks.card(snap)
        self.assertIn("Nothing to fix that DMbot can see", text)
        self.assertTrue(text.endswith("You know your character best."))

    def test_a_huge_sheet_still_fits_one_message_with_its_footer(self) -> None:
        snap = sheets.parse(belleros())
        snap["spells"] = [f"Homebrew Spell Number {i}" for i in range(100)]
        snap["feats"] = [f"Odd Feat {i}" for i in range(100)]
        snap["hp"] = 999
        snap["features"] = ["F" * 59] * 3
        text = checks.card(snap)
        self.assertLessEqual(len(text), checks.CARD_MAX)
        self.assertIn(" more", text)
        self.assertTrue(text.endswith("DMbot has changed nothing."))  # the footer is never cut

    def test_the_card_for_a_sheet_with_a_problem_says_what_to_do(self) -> None:
        snap = {**sheets.parse(belleros()), "hp": 99}
        text = checks.card(snap)
        lines = text.split("\n")
        self.assertIn("1 thing to check", lines[0])
        self.assertTrue(lines[1].startswith("⛔ Character:"))  # first
        self.assertIn("Check HP on your sheet", lines[1])
        self.assertIn("⚠️ is not a problem", text)  # a warning's meaning still shows with a ⛔
        self.assertTrue(text.endswith("DMbot has changed nothing."))

    def test_the_names_are_escaped(self) -> None:
        text = checks.card({**sheets.parse(belleros()), "name": "Bel*le_ros"})
        self.assertIn("Bel\\*le\\_ros", text.split("\n")[0])

    def test_plurals_and_no_languages(self) -> None:
        snap = {**sheets.parse(belleros()), "languages": [], "tools": ["Herbalism Kit"]}
        parts = {c.part: c.text for c in checks.checks(snap)}
        self.assertIn("1 tool,", parts["proficiencies"])
        self.assertIn("no languages found", parts["more"])
        self.assertIn("1 sense", parts["more"])

    def test_a_typo_near_a_known_feat_is_asked_about(self) -> None:
        snap = {**sheets.parse(belleros()), "feats": ["Sharpshoter"]}
        feats = {c.part: c.text for c in checks.checks(snap)}["feats"]
        self.assertIn("did you mean Sharpshooter?", feats)

    def test_hit_points_with_no_damage_known_are_not_shown_as_current(self) -> None:
        snap = {**sheets.parse(belleros()), "hp": None}
        self.assertEqual(checks.vitals(snap), "max HP 26, AC 18")

    def test_a_reread_sends_a_card_only_if_something_changed(self) -> None:
        old = sheets.parse(belleros())
        self.assertFalse(checks.changed(old, copy.deepcopy(old)))
        self.assertTrue(checks.changed(old, {**old, "hp": 3}))
        self.assertTrue(checks.changed(None, old))

    def test_vitals_for_the_dms_card(self) -> None:
        snap = sheets.parse(belleros())
        self.assertEqual(checks.vitals(snap), "HP 11 / 26 + 0, AC 18")
        self.assertEqual(
            checks.vitals({"max_hp": 26, "hp": 11, "temp_hp": 5, "ac": 18}), "HP 11 / 26 + 5, AC 18"
        )
        self.assertEqual(checks.vitals({}), "")
