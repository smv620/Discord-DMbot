"""The rules index (#866): names, aliases and the order of rulesets, on made-up data; and
the shipped SRD 5.2.1 data: complete, cited, attributed, and nothing from outside it."""

from __future__ import annotations

import itertools
import json
import re
import subprocess
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from dmbot.campaigns.models import FALLBACK_NONE
from dmbot.devtools.srd import build, parse51
from dmbot.rules import aliases, index
from dmbot.rules.index import DATA, Entry, Hit, Index, edition_tag, normalize

real_load = index.load_folder

ATTRIBUTION_51 = (
    "This work includes material taken from the System Reference Document 5.1 (“SRD 5.1”) by "
    "Wizards of the Coast LLC and available at "
    "https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed "
    "under the Creative Commons Attribution 4.0 International License available at "
    "https://creativecommons.org/licenses/by/4.0/legalcode."
)
FILES = ("ATTRIBUTION.md", "conditions.json", "features.json", "monsters.json", "spells.json")
FILES_52 = (
    "ATTRIBUTION.md",
    "conditions.json",
    "equipment.json",
    "feats.json",
    "features.json",
    "items.json",
    "monsters.json",
    "origins.json",
    "rules.json",
    "spells.json",
)


def all_text(entry: Entry) -> str:
    """Everything an entry says in words: its text and its casting time, range, components
    and duration."""
    fields = ("casting_time", "range", "duration")
    parts = [entry.text, *(str(entry.details[k]) for k in fields if k in entry.details)]
    if "components" in entry.details:  # without the letters V, S and M
        parts.append(re.sub(r"\b[VSM]\b,? ?", "", str(entry.details["components"])))
    return "\n".join(parts)


def srd_entries(edition: str, kinds: tuple[str, ...] = ("spell", "condition")) -> list[Entry]:
    """What `srd()` holds for one edition (spells and conditions unless `kinds` says)."""
    return [e for e in index.srd().entries if e.edition == edition and e.kind in kinds]


REPO = Path(__file__).resolve().parents[2]
ATTRIBUTION = (
    "This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by "
    "Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is "
    "licensed under the Creative Commons Attribution 4.0 International License, available "
    "at https://creativecommons.org/licenses/by/4.0/legalcode."
)


def spell(name: str, edition: str = "2024", page: int = 100, text: str = "words") -> Entry:
    legacy = edition == "2014"
    return Entry(
        "spell",
        name,
        edition,
        "SRD 5.1" if legacy else "SRD 5.2.1",
        "Spells" if legacy else "Spell Descriptions",
        page,
        text,
        {"level": 2},
    )


class Names(unittest.TestCase):
    def test_case_punctuation_and_apostrophes_do_not_matter(self) -> None:
        keys = {
            normalize(n)
            for n in (
                "Melf’s Acid Arrow",
                "Melf's acid arrow",
                "melfs ACID arrow",
                "  Melf's  Acid-Arrow ",
                "Melfʼs Acid Arrow",
            )
        }
        self.assertEqual(keys, {"melfs acid arrow"})

    def test_slashes_and_hyphens_separate_words(self) -> None:
        self.assertEqual(normalize("Enlarge/Reduce"), "enlarge reduce")
        self.assertEqual(normalize("Antipathy/Sympathy"), "antipathy sympathy")
        self.assertEqual(normalize("Blindness/Deafness"), normalize("blindness deafness"))

    def test_nothing_in_a_name_means_no_name(self) -> None:
        self.assertEqual(normalize(" ’ ' / "), "")
        self.assertIsNone(Index([spell("Fireball")]).lookup("  ", "2024"))


class Order(unittest.TestCase):
    """Target ruleset first, then the fallback; an older entry only when no newer one
    matches any name."""

    def setUp(self) -> None:
        self.new = spell("Acid Arrow", "2024", page=107)
        self.old = spell("Melf’s Acid Arrow", "2014", page=7)
        self.both = spell("Fireball", "2024", page=131)
        self.old_fireball = spell("Fireball", "2014", page=40)
        self.only_old = spell("Sleet Storm", "2014", page=60)
        self.index = Index(
            [self.new, self.old, self.both, self.old_fireball, self.only_old],
            [("Melf’s Acid Arrow", "Acid Arrow")],
        )

    def test_the_newest_is_found_first_and_has_no_tag(self) -> None:
        hit = self.index.lookup("fireball", "2024", "2014")
        assert hit is not None
        self.assertEqual((hit.entry, hit.tag), (self.both, ""))
        self.assertEqual(hit.citation, "SRD 5.2.1, Spell Descriptions, p. 131")

    def test_an_older_name_finds_the_newer_entry_not_the_legacy_one(self) -> None:
        hit = self.index.lookup("Melf's Acid Arrow", "2024", "2014")
        assert hit is not None
        self.assertEqual(hit.entry, self.new)  # not the 2014 entry of the same name
        self.assertEqual(hit.tag, "")
        self.assertTrue(hit.renamed)
        self.assertFalse(self.index.lookup("Acid Arrow", "2024", "2014").renamed)  # type: ignore[union-attr]

    def test_the_fallback_is_used_only_when_the_target_has_nothing_and_is_tagged(self) -> None:
        hit = self.index.lookup("Sleet Storm", "2024", "2014")
        assert hit is not None
        self.assertEqual((hit.entry, hit.tag), (self.only_old, "[Legacy 2014]"))
        self.assertEqual(hit.citation, "SRD 5.1, Spells, p. 60 [Legacy 2014]")

    def test_with_no_fallback_the_older_rules_are_not_looked_in(self) -> None:
        self.assertIsNone(self.index.lookup("Sleet Storm", "2024", FALLBACK_NONE))
        self.assertIsNone(self.index.lookup("Sleet Storm", "2024"))  # the default is none

    def test_a_2014_campaign_finds_2014_first_and_fallback_2024_is_tagged_by_edition(self) -> None:
        hit = self.index.lookup("Fireball", "2014", "2024")
        assert hit is not None
        self.assertEqual(hit.entry, self.old_fireball)
        self.assertEqual(hit.tag, "[Legacy 2014]")  # the 2014 rules are legacy wherever they show
        hit = self.index.lookup("Acid Arrow", "2014", "2024")
        assert hit is not None
        self.assertEqual((hit.entry, hit.tag), (self.new, "[2024]"))  # from the fallback

    def test_the_same_ruleset_twice_is_looked_in_once(self) -> None:
        self.assertIsNone(self.index.lookup("Sleet Storm", "2024", "2024"))

    def test_an_unknown_name_is_none(self) -> None:
        self.assertIsNone(self.index.lookup("Wish upon a star", "2024", "2014"))

    def test_the_kind_limits_the_search(self) -> None:
        charmed = Entry("condition", "Charmed", "2024", "SRD 5.2.1", "Rules Glossary", 178, "x")
        both = Index([charmed, spell("Charmed")])
        hit = both.lookup("charmed", "2024", kind="spell")
        assert hit is not None
        self.assertEqual(hit.entry.kind, "spell")
        hit = both.lookup("charmed", "2024", kind="condition")
        assert hit is not None
        self.assertEqual(hit.entry.kind, "condition")
        self.assertIsNone(Index([charmed]).lookup("charmed", "2024", kind="spell"))

    def test_an_alias_to_nothing_is_a_mistake_that_stops_the_build(self) -> None:
        with self.assertRaisesRegex(ValueError, "isn't in the data"):
            Index([spell("Fireball")], [("Old Name", "No Such Spell")])

    def test_tags(self) -> None:
        self.assertEqual(edition_tag("2014", from_fallback=False), "[Legacy 2014]")
        self.assertEqual(edition_tag("2014", from_fallback=True), "[Legacy 2014]")
        self.assertEqual(edition_tag("2024", from_fallback=True), "[2024]")
        self.assertEqual(edition_tag("2024", from_fallback=False), "")


class TheRulesGlossary(unittest.TestCase):
    """Glossary entries (#1884) are found only when their kind is asked for."""

    def setUp(self) -> None:
        self.cover = Entry("rule", "Cover", "2024", "SRD 5.2.1", "Rules Glossary", 179, "Cover.")
        self.fire = spell("Fireball", "2024", page=131)
        self.index = Index([self.cover, self.fire])

    def test_it_is_found_by_its_kind_and_not_without_one(self) -> None:
        hit = self.index.lookup("cover", "2024", kind="rule")
        assert hit is not None
        self.assertEqual(hit.entry, self.cover)
        self.assertEqual(hit.citation, "SRD 5.2.1, Rules Glossary, p. 179")
        self.assertIsNone(self.index.lookup("cover", "2024"))
        self.assertIsNone(self.index.lookup("cover", "2024", kind="spell"))

    def test_its_name_is_never_offered_to_spot_complete_or_suggest(self) -> None:
        self.assertNotIn("cover", self.index.names_pool("2024"))
        self.assertEqual([e.name for e in self.index.typeahead("co", "2024")], [])
        self.assertEqual(self.index.suggest("covr", "2024"), [])

    def test_a_spell_of_the_same_name_is_not_hidden_by_it(self) -> None:
        spell_cover = spell("Cover", "2024", page=5)
        index = Index([self.cover, spell_cover])
        hit = index.lookup("cover", "2024")
        assert hit is not None
        self.assertEqual(hit.entry, spell_cover)
        self.assertIn("cover", index.names_pool("2024"))

    def test_the_shipped_glossary_is_cited_and_read_only(self) -> None:
        rules = [
            e for e in index.srd().entries if e.kind == "rule" and e.section == "Rules Glossary"
        ]  # (the weapon properties are rules of the "Equipment" section, #1919)
        self.assertEqual(len(rules), 140)
        self.assertTrue(all(e.edition == "2024" and e.section == "Rules Glossary" for e in rules))
        self.assertFalse([e.name for e in rules if not e.text or e.page < 176 or e.page > 191])
        self.assertFalse([e.name for e in rules if e.details.get("tag") == "Condition"])
        cover = index.srd().lookup("Cover", "2024", kind="rule")
        assert cover is not None
        self.assertEqual(cover.citation, "SRD 5.2.1, Rules Glossary, p. 179")
        self.assertIsInstance(cover.entry.details["see_also"], tuple)
        self.assertEqual(index.srd().lookup("Half Cover", "2024", kind="rule"), None)

    def test_a_list_set_as_a_table_and_a_wrapped_bullet_read_cleanly(self) -> None:
        action = index.srd().lookup("Action", "2024", kind="rule")
        area = index.srd().lookup("Area of Effect", "2024", kind="rule")
        terrain = index.srd().lookup("Difficult Terrain", "2024", kind="rule")
        assert action and area and terrain
        self.assertNotIn("Influence", action.entry.text)  # the action list is a table
        self.assertNotIn("Cylinder Line", area.entry.text)
        self.assertIn(
            "• Furniture that is sized for creatures of your size or larger", terrain.entry.text
        )
        glued = [
            e.name
            for e in index.srd().entries
            if e.kind == "rule" and re.search(r"[a-z][A-Z]", e.text)
        ]
        self.assertEqual(glued, [])

    def test_a_table_is_not_part_of_an_entrys_text(self) -> None:
        carrying = index.srd().lookup("Carrying Capacity", "2024", kind="rule")
        assert carrying is not None
        self.assertNotIn("Drag/Lift/Push", carrying.entry.text)

    def test_a_missing_rules_file_is_logged_once_and_the_rest_still_loads(self) -> None:
        index.srd.cache_clear()
        try:
            with (
                self.assertLogs(index.log, "WARNING") as logged,
                mock.patch.object(
                    index,
                    "load_folder",
                    side_effect=lambda f: [e for e in real_load(f) if e.kind != "rule"],
                ),
            ):
                found = index.srd()
                index.srd()  # cached: no second warning
            self.assertEqual(len(logged.records), 1)
            self.assertIsNotNone(found.lookup("Fireball", "2024"))
            self.assertIsNone(found.lookup("Cover", "2024", kind="rule"))
        finally:
            index.srd.cache_clear()


class ClassFeatures(unittest.TestCase):
    """Class features (#1900): found only when asked for, one entry per class."""

    def test_rage_is_the_barbarians(self) -> None:
        (hit,) = index.srd().lookup_all("Rage", "2024", "2014", kind="feature")
        self.assertEqual((hit.entry.details["class"], hit.entry.details["level"]), ("Barbarian", 1))
        self.assertEqual(hit.citation, "SRD 5.2.1, Classes, p. 28")
        self.assertIsNone(index.srd().lookup("Rage", "2024", "2014"))  # not without a kind

    def test_a_name_two_classes_share_gives_both_and_a_class_picks_one(self) -> None:
        both = index.srd().lookup_all("Evasion", "2024", "2014", kind="feature")
        self.assertEqual(sorted(h.entry.details["class"] for h in both), ["Monk", "Rogue"])
        (rogue,) = index.srd().lookup_all("Evasion", "2024", "2014", kind="feature", prefer="rogue")
        self.assertEqual(rogue.entry.details["class"], "Rogue")
        # a class that does not have it gives them all, not nothing
        nobody = index.srd().lookup_all("Evasion", "2024", "2014", kind="feature", prefer="bard")
        self.assertEqual(len(nobody), 2)

    def test_the_names_are_kept_out_of_the_pool_and_the_list(self) -> None:
        srd = index.srd()
        self.assertNotIn("rage", srd.names_pool("2024"))
        self.assertEqual([e for e in srd.typeahead("evasi", "2024") if e.kind == "feature"], [])
        listed = srd.asked_names("evasi", "2024", kinds=("feature",))
        self.assertEqual([e.details["class"] for e in listed], ["Monk", "Rogue"])

    def test_a_subclass_feature_knows_its_subclass_and_a_spell_of_the_name_still_wins(self) -> None:
        (frenzy,) = index.srd().lookup_all("Frenzy", "2024", "2014", kind="feature")
        self.assertEqual(frenzy.entry.details["subclass"], "Path of the Berserker")
        spell = index.srd().lookup("Fireball", "2024", "2014")
        assert spell is not None
        self.assertEqual(spell.entry.kind, "spell")

    def test_the_shipped_features_are_complete_and_read_only(self) -> None:
        for edition, total, pages, barbarian in (
            ("2024", 220, (28, 82), 23),
            ("2014", 179, (8, 56), 18),
        ):
            features = [
                e for e in index.srd().entries if e.kind == "feature" and e.edition == edition
            ]
            by_class: dict[str, int] = {}
            for e in features:
                by_class[str(e.details["class"])] = by_class.get(str(e.details["class"]), 0) + 1
                self.assertTrue(e.text and pages[0] <= e.page <= pages[1], (edition, e.name))
            self.assertEqual(len(features), total, edition)
            self.assertEqual(len(by_class), 12, edition)
            # Barbarian: 2024 has 19 class features and 4 of the Berserker; 2014 has 14 and 4.
            self.assertEqual(by_class["Barbarian"], barbarian, edition)

    def test_the_shipped_magic_items_are_complete_and_found_only_when_asked_for(self) -> None:
        srd = index.srd()
        items = [e for e in srd.entries if e.kind == "item"]
        self.assertEqual(len(items), 258)
        self.assertEqual({e.edition for e in items}, {"2024"})
        self.assertEqual(len({e.name for e in items}), len(items))
        for e in items:
            with self.subTest(e.name):
                self.assertTrue(e.text and 209 <= e.page <= 253)
                self.assertTrue(e.details["category"] and e.details["rarity"])
        # never offered as a name to spot in talk, to suggest or to complete: "Bag" is a word
        self.assertFalse(any(e.kind == "item" for e in srd.names_pool("2024", "2014").values()))
        self.assertIsNone(srd.lookup("Bag of Holding", "2024", "2014"))
        self.assertEqual([e for e in srd.typeahead("bag", "2024", "2014") if e.kind == "item"], [])
        close = srd.suggest("bag of hold", "2024", "2014")
        self.assertEqual([e for e in close if e.kind == "item"], [])
        found = srd.lookup("bag of holding", "2024", "2014", kind="item")
        assert found is not None
        self.assertEqual(found.entry.citation, "SRD 5.2.1, Magic Items, p. 212")

    def test_the_shipped_feats_species_and_backgrounds_are_complete(self) -> None:
        def of(kind: str) -> list[Entry]:
            return [e for e in index.srd().entries if e.kind == kind]

        self.assertEqual((len(of("feat")), len(of("species")), len(of("background"))), (17, 9, 4))
        for e in [*of("feat"), *of("species"), *of("background")]:
            with self.subTest(e.name):
                self.assertTrue(e.text and e.edition == "2024")
        srd = index.srd()
        # none of the three is a name to spot in talk, suggest or complete
        pool = srd.names_pool("2024", "2014").values()
        self.assertFalse(any(e.kind in ("feat", "species", "background") for e in pool))
        self.assertIsNone(srd.lookup("Alert", "2024", "2014"))
        self.assertEqual([e.name for e in srd.typeahead("aler", "2024", "2014")], [])

    def test_a_species_paragraph_across_a_column_break_is_whole_and_a_dropped_table_is_said(
        self,
    ) -> None:
        gnome = index.srd().lookup("Gnome", "2024", "2014", kind="species")
        assert gnome is not None
        self.assertIn("produces that effect whenever you or another creature", gnome.entry.text)
        for name, table in (
            ("Dragonborn", "Draconic Ancestors"),
            ("Elf", "Elven Lineages"),
            ("Tiefling", "Fiendish Legacies"),
        ):
            hit = index.srd().lookup(name, "2024", "2014", kind="species")
            assert hit is not None
            self.assertTrue(hit.entry.text.endswith(f"(The {table} table is not included here.)"))

    def test_golden_feats_species_and_backgrounds(self) -> None:
        def get(name: str, kind: str) -> Entry:
            hit = index.srd().lookup(name, "2024", "2014", kind=kind)
            assert hit is not None, name
            return hit.entry

        self.assertEqual(get("Alert", "feat").details["category"], "Origin")
        grappler = get("Grappler", "feat")
        self.assertEqual(grappler.details["prerequisite"], "Level 4+, Strength or Dexterity 13+")
        self.assertEqual(get("Goliath", "species").details["speed"], "35 feet")
        sage = get("Sage", "background")
        self.assertEqual(sage.details["feat"], "Magic Initiate (Wizard)")
        self.assertEqual(sage.details["skills"], "Arcana and History")
        self.assertEqual(sage.citation, "SRD 5.2.1, Character Origins, p. 83")
        self.assertEqual(get("Alert", "feat").citation, "SRD 5.2.1, Feats, p. 87")
        for background in (e for e in index.srd().entries if e.kind == "background"):
            feat = index.srd().lookup(background.details["feat"], "2024", "2014", kind="feat")
            self.assertIsNotNone(feat, background.name)  # the feat each background gives exists

    def test_the_shipped_equipment_is_complete_and_found_only_when_asked_for(self) -> None:
        srd = index.srd()

        def of(kind: str, section: str | None = None) -> list[Entry]:
            return [
                e
                for e in srd.entries
                if e.kind == kind and (section is None or e.section == section)
            ]

        self.assertEqual((len(of("weapon")), len(of("armor"))), (38, 13))
        self.assertEqual(len(of("rule", "Equipment")), 18)
        self.assertEqual(len(of("rule", "Rules Glossary")), 140)  # the glossary is untouched
        names = [e.name for e in of("rule")]
        self.assertEqual(len(names), len(set(names)))  # no property shadows a glossary entry
        pool = srd.names_pool("2024", "2014").values()
        self.assertFalse(any(e.kind in ("weapon", "armor") for e in pool))
        self.assertIsNone(srd.lookup("Longsword", "2024", "2014"))
        shield = srd.lookup("Shield", "2024", "2014")
        assert shield is not None
        self.assertEqual(shield.entry.kind, "spell")  # the spell still answers a bare "Shield"

    def test_golden_weapons_and_armor(self) -> None:
        def get(name: str, kind: str) -> Entry:
            hit = index.srd().lookup(name, "2024", "2014", kind=kind)
            assert hit is not None, name
            return hit.entry

        from dmbot.rules import facts

        longsword = get("Longsword", "weapon")
        self.assertEqual(
            facts.weapon_line(longsword),
            "Martial Melee, 1d8 Slashing, Versatile (1d10), mastery Sap",
        )
        dagger = get("Dagger", "weapon")
        self.assertEqual(
            (dagger.details["properties"], dagger.details["mastery"]),
            ("Finesse, Light, Thrown (Range 20/60)", "Nick"),
        )
        plate = get("Plate Armor", "armor")
        self.assertEqual(
            (plate.details["ac"], plate.details["strength"], plate.details["stealth"]),
            ("18", "Str 15", "Disadvantage"),
        )
        self.assertEqual(
            facts.armor_line(plate),
            "Heavy armor, AC 18, needs Strength 15, Disadvantage on Stealth",
        )
        self.assertEqual(get("Shield", "armor").details["ac"], "+2")
        self.assertEqual(get("Topple", "rule").details["tag"], "Mastery Property")
        self.assertEqual(get("Finesse", "rule").citation, "SRD 5.2.1, Equipment, p. 89")

    def test_the_shipped_tools_gear_mounts_and_vehicles_are_complete_and_asked_for(self) -> None:
        srd = index.srd()
        tools = [e for e in srd.entries if e.kind == "tool"]
        gear = [e for e in srd.entries if e.kind == "gear"]
        self.assertEqual(len(tools), 25)
        by_group = {
            g: [e for e in gear if e.details["group"] == g]
            for g in ("Adventuring Gear", "Mount", "Vehicle")
        }
        self.assertEqual(
            {g: len(v) for g, v in by_group.items()},
            {"Adventuring Gear": 81, "Mount": 8, "Vehicle": 12},
        )
        for kind in ("tool", "gear"):
            names = [e.name for e in srd.entries if e.kind == kind]
            self.assertEqual(len(names), len(set(names)), kind)
        pool = srd.names_pool("2024", "2014").values()
        self.assertFalse(any(e.kind in ("tool", "gear") for e in pool))
        self.assertIsNone(srd.lookup("Rope", "2024", "2014"))  # found only when asked for
        self.assertIsNotNone(srd.lookup("Rope", "2024", "2014", kind="gear"))

    def test_golden_tools_and_gear(self) -> None:
        def get(name: str, kind: str) -> Entry:
            hit = index.srd().lookup(name, "2024", "2014", kind=kind)
            assert hit is not None, name
            return hit.entry

        from dmbot.rules import facts

        kit = get("Healer’s Kit", "gear")
        self.assertIn("ten uses", kit.text)
        self.assertIn("stabilize", kit.text)
        self.assertEqual((kit.details["cost"], kit.details["weight"]), ("5 GP", "3 lb."))
        thieves = get("Thieves’ Tools", "tool")
        self.assertEqual(
            (thieves.details["ability"], thieves.details["weight"], thieves.details["cost"]),
            ("Dexterity", "1 lb.", "25 GP"),
        )
        self.assertEqual(
            facts.tool_line(thieves, card=False), "Other Tools, Dexterity, 25 GP, 1 lb."
        )
        rope = get("Rope", "gear")
        self.assertEqual((rope.details["weight"], rope.details["cost"]), ("5 lb.", "1 GP"))
        horse = get("Horse, Riding", "gear")
        self.assertEqual(
            (horse.details["cost"], horse.details["capacity"], horse.text),
            ("75 GP", "480 lb.", "Carrying capacity 480 lb."),
        )
        self.assertEqual(get("Rowboat", "gear").details["speed"], "1½ mph")
        self.assertEqual(
            get("Spell Scroll", "gear").details["cost"], "Cantrip, 30 GP; Level 1, 50 GP"
        )

    def test_a_fact_line_with_a_weight_does_not_double_its_full_stop(self) -> None:
        from dmbot.rules import facts

        kit = index.srd().lookup("Healer’s Kit", "2024", "2014", kind="gear")
        assert kit is not None
        text = facts.full_text(kit.entry)
        self.assertTrue(text.startswith("Adventuring Gear, 5 GP, 3 lb. A Healer’s Kit"), text)
        self.assertNotIn("..", text)
        focus = index.srd().lookup("Arcane Focus", "2024", "2014", kind="gear")
        assert focus is not None
        self.assertEqual(facts.gear_line(focus.entry, card=True), "weight varies")
        cart = index.srd().lookup("Cart", "2024", "2014", kind="gear")
        assert cart is not None
        self.assertEqual(facts.gear_line(cart.entry, card=True), "")  # its text says the weight
        thieves = index.srd().lookup("Thieves’ Tools", "2024", "2014", kind="tool")
        assert thieves is not None
        self.assertEqual(facts.tool_line(thieves.entry, card=True), "25 GP, 1 lb.")

    def test_gear_is_found_by_its_other_names(self) -> None:
        self.assertEqual(index.gear_keys("Horse, Riding"), ["riding horse"])
        self.assertEqual(index.gear_keys("Healer’s Kit"), ["healer kit"])
        self.assertEqual(
            index.gear_keys("Clothes, Traveler’s"),
            ["travelers clothes", "clothes traveler", "traveler clothes"],
        )
        self.assertEqual(index.gear_keys("Rope"), [])
        found = index.srd().lookup("riding horse", "2024", "2014", kind="gear")
        assert found is not None
        self.assertEqual(found.entry.name, "Horse, Riding")

    def test_every_shipped_rarity_is_one_the_srd_prints(self) -> None:
        from dmbot.devtools.srd import items as reader

        for e in index.srd().entries:
            if e.kind == "item":
                with self.subTest(e.name):
                    self.assertRegex(e.details["rarity"], reader.RARITIES)
        sending = index.srd().lookup("Sending Stones", "2024", "2014", kind="item")
        assert sending is not None
        self.assertTrue(sending.entry.text.startswith("Sending Stones come in pairs"))

    def test_the_attribution_names_every_item_with_lines_left_out(self) -> None:
        text = (index.DATA / "srd52" / "ATTRIBUTION.md").read_text(encoding="utf-8")
        self.assertIn("The 41 items with something left out are:", text)
        for name in ("Bag of Beans", "Spell Scroll", "Wand of Wonder", "Potions of Healing"):
            self.assertIn(name, text)

    def test_potion_of_healing_finds_potions_of_healing(self) -> None:
        hit = index.srd().lookup("potion of healing", "2024", "2014", kind="item")
        assert hit is not None
        self.assertEqual(hit.entry.name, "Potions of Healing")

    def test_golden_items(self) -> None:
        def item(name: str) -> Entry:
            hit = index.srd().lookup(name, "2024", "2014", kind="item")
            assert hit is not None, name
            return hit.entry

        bag, cloak = item("Bag of Holding"), item("Cloak of Protection")
        hat = item("Hat of Many Spells")
        self.assertEqual((bag.details["rarity"], bag.details["attunement"]), ("Uncommon", False))
        self.assertEqual((cloak.details["rarity"], cloak.details["attunement"]), ("Uncommon", True))
        self.assertEqual(
            (hat.details["attunement"], hat.details["attunement_by"]), (True, "a Wizard")
        )
        armor = item("Armor, +1, +2, or +3")
        self.assertEqual(armor.details["rarity"], "Rare (+1), Very Rare (+2), or Legendary (+3)")

    def test_a_rarity_in_a_heading_is_one_short_phrase(self) -> None:
        from dmbot.rules import facts

        self.assertEqual(facts.short_rarity("Uncommon"), "uncommon")
        self.assertEqual(facts.short_rarity("Very Rare"), "very rare")
        self.assertEqual(
            facts.short_rarity("Uncommon (+1), Rare (+2), or Very Rare (+3)"),
            "uncommon to very rare",
        )
        self.assertEqual(facts.short_rarity("Rarity Varies"), "")  # no rarity, so none is shown

    def test_an_item_has_its_other_names(self) -> None:
        self.assertEqual(
            index.item_keys("Stone of Good Luck (Luckstone)"), ["stone of good luck", "luckstone"]
        )
        self.assertEqual(
            index.item_keys("Wand of the War Mage, +1, +2, or +3"), ["wand of the war mage"]
        )
        self.assertEqual(index.item_keys("Weapon, +1, +2, or +3"), [])  # "weapon": everyday word
        self.assertEqual(index.item_keys("Bag of Holding"), [])

    def test_no_feature_is_cut_short_and_no_placeholder_is_kept(self) -> None:
        # Sneak Attack, Spellcasting and Eldritch Invocations were cut where a table sits in
        # the column (found in review): every text ends a sentence.
        for e in index.srd().entries:
            if e.kind != "feature":
                continue
            with self.subTest(e.name, klass=e.details["class"]):
                ends = r"[.)\":!?”]\s*$"
                if e.edition == "2014" and e.name.endswith("Spellcasting"):
                    # the 5.1 spellcasting features end on their formula: "... your Wisdom modifier"
                    ends += r"|\byour \w+ modifier\s*$"
                self.assertRegex(e.text, ends)
                self.assertNotEqual(e.name, f"{e.details['class']} Subclass")
        sneak = index.srd().lookup_all("Sneak Attack", "2024", kind="feature")[0].entry
        self.assertIn("Advantage", sneak.text)  # the part after the table

    def test_the_2014_features_are_used_only_where_there_is_no_newer_one(self) -> None:
        srd = index.srd()
        # Brutal Critical is only in the 2014 rules: it is found, tagged.
        (brutal,) = srd.lookup_all("Brutal Critical", "2024", "2014", kind="feature")
        self.assertEqual((brutal.entry.edition, brutal.tag), ("2014", "[Legacy 2014]"))
        # Rage is in both: the target's version, and only that.
        (rage,) = srd.lookup_all("Rage", "2024", "2014", kind="feature")
        self.assertEqual((rage.entry.edition, rage.tag), ("2024", ""))
        (older,) = srd.lookup_all("Rage", "2014", "2024", kind="feature")
        self.assertEqual(older.entry.edition, "2014")
        # Timeless Body is only in the 2014 rules (for the Druid and the Monk): both are found.
        both = srd.lookup_all("Timeless Body", "2024", "2014", kind="feature")
        self.assertEqual(
            sorted((h.entry.details["class"], h.tag) for h in both),
            [("Druid", "[Legacy 2014]"), ("Monk", "[Legacy 2014]")],
        )

    def test_a_class_the_target_lacks_gets_its_older_feature_and_the_class_it_has_gets_the_new(
        self,
    ) -> None:
        def feature(edition: str, klass: str) -> Entry:
            return Entry(
                "feature", "Aura", edition, "SRD", "Classes", 1, f"{klass} {edition}",
                {"class": klass, "level": 1},
            )  # fmt: skip

        srd = Index(
            [feature("2024", "Paladin"), feature("2014", "Paladin"), feature("2014", "Cleric")]
        )
        hits = srd.lookup_all("Aura", "2024", "2014", kind="feature")
        self.assertEqual(
            [(h.entry.details["class"], h.entry.edition, h.from_fallback) for h in hits],
            [("Paladin", "2024", False), ("Cleric", "2014", True)],
        )
        (cleric,) = srd.lookup_all("Aura", "2024", "2014", kind="feature", prefer="cleric")
        self.assertEqual(cleric.tag, "[Legacy 2014]")
        listed = srd.asked_names("aura", "2024", "2014", kinds=("feature",))
        self.assertEqual(
            [(e.details["class"], e.edition) for e in listed],
            [("Paladin", "2024"), ("Cleric", "2014")],
        )

    def test_a_2014_campaign_finds_its_own_features_and_the_newer_ones_only_for_a_class_it_lacks(
        self,
    ) -> None:
        def feature(edition: str, klass: str) -> Entry:
            return Entry(
                "feature", "Aura", edition, "SRD", "Classes", 1, f"{klass} {edition}",
                {"class": klass, "level": 1},
            )  # fmt: skip

        srd = Index(
            [feature("2024", "Paladin"), feature("2014", "Paladin"), feature("2024", "Cleric")]
        )
        hits = srd.lookup_all("Aura", "2014", "2024", kind="feature")
        self.assertEqual(
            [(h.entry.details["class"], h.entry.edition, h.from_fallback) for h in hits],
            [("Paladin", "2014", False), ("Cleric", "2024", True)],
        )

    def test_the_levels_of_the_2014_features_match_the_published_class_tables(self) -> None:
        # Read by hand from the SRD 5.1 class tables; these are the ones the table's wrapped and
        # split rows made hardest (the Monk's table is printed in two pieces).
        golden = {
            ("Monk", "Ki"): 2,
            ("Monk", "Unarmored Movement"): 2,
            ("Monk", "Evasion"): 7,
            ("Monk", "Stillness of Mind"): 7,
            ("Warlock", "Mystic Arcanum"): 11,
            ("Paladin", "Aura of Protection"): 6,
            ("Rogue", "Thieves’ Cant"): 1,
            ("Ranger", "Land’s Stride"): 8,
            ("Barbarian", "Brutal Critical"): 9,
        }
        found = {
            (str(e.details["class"]), e.name): e.details["level"]
            for e in index.srd().entries
            if e.kind == "feature" and e.edition == "2014" and not e.details.get("subclass")
        }
        for key, level in golden.items():
            with self.subTest(key):
                self.assertEqual(found.get(key), level)

    def test_the_name_list_does_not_offer_the_older_version_of_what_the_target_has(self) -> None:
        names = index.srd().asked_names("evasion", "2024", "2014", kinds=("feature",), limit=25)
        self.assertEqual(
            sorted((e.details["class"], e.edition) for e in names),
            [("Monk", "2024"), ("Rogue", "2024")],
        )
        older = index.srd().asked_names("brutal crit", "2024", "2014", kinds=("feature",))
        self.assertEqual([(e.name, e.edition) for e in older], [("Brutal Critical", "2014")])

    def test_its_facts_line_names_the_class_level_and_subclass(self) -> None:
        (rage,) = index.srd().lookup_all("Rage", "2024", kind="feature")
        self.assertEqual(rage.entry.facts, "Class feature, Barbarian level 1")
        (frenzy,) = index.srd().lookup_all("Frenzy", "2024", kind="feature")
        self.assertEqual(
            frenzy.entry.facts, "Class feature, Barbarian level 3, Path of the Berserker"
        )


class TheShippedData(unittest.TestCase):
    """What DMbot actually ships, as `srd()` loads it."""

    srd: Index
    spells: list[Entry]
    conditions: list[Entry]

    @classmethod
    def setUpClass(cls) -> None:
        cls.srd = index.srd()
        cls.spells = [e for e in srd_entries("2024") if e.kind == "spell"]
        cls.conditions = [e for e in srd_entries("2024") if e.kind == "condition"]

    def test_every_spell_and_condition_of_the_srd_is_there(self) -> None:
        self.assertEqual((len(self.spells), len(self.conditions)), (339, 15))
        names = {e.name for e in self.conditions}
        self.assertEqual(
            names,
            {
                "Blinded", "Charmed", "Deafened", "Exhaustion", "Frightened", "Grappled",
                "Incapacitated", "Invisible", "Paralyzed", "Petrified", "Poisoned", "Prone",
                "Restrained", "Stunned", "Unconscious",
            },
        )  # fmt: skip
        self.assertEqual(len({normalize(e.name) for e in self.spells}), 339)  # no repeats

    def test_known_spells_are_found_with_what_an_alert_needs(self) -> None:
        hit = self.srd.lookup("fireball", "2024", "2014", kind="spell")
        assert hit is not None
        fireball = hit.entry
        self.assertEqual(
            dict(fireball.details),
            {
                "level": 3,
                "school": "Evocation",
                "classes": ("Sorcerer", "Wizard"),
                "casting_time": "Action",
                "range": "150 feet",
                "components": "V, S, M (a ball of bat guano and sulfur)",
                "duration": "Instantaneous",
            },
        )
        self.assertIn("8d6 Fire damage", fireball.text)
        self.assertEqual(hit.citation, f"SRD 5.2.1, Spell Descriptions, p. {fireball.page}")
        blinded = self.srd.lookup("Blinded", "2024", kind="condition")
        assert blinded is not None
        self.assertIn("Can’t See.", blinded.entry.text)
        self.assertEqual(blinded.entry.section, "Rules Glossary")

    def test_cantrips_are_level_zero_and_levels_and_schools_are_real(self) -> None:
        schools = {
            "Abjuration", "Conjuration", "Divination", "Enchantment", "Evocation", "Illusion",
            "Necromancy", "Transmutation",
        }  # fmt: skip
        for e in self.spells:
            self.assertIn(e.details["level"], range(10), e.name)
            self.assertIn(e.details["school"], schools, e.name)
            self.assertTrue(e.details["classes"], e.name)
        cantrips = [e for e in self.spells if e.details["level"] == 0]
        self.assertIn("Acid Splash", {e.name for e in cantrips})
        self.assertGreater(len(cantrips), 15)

    def test_every_entry_has_a_citation_and_words(self) -> None:
        for e in srd_entries("2024"):
            with self.subTest(e.name):
                self.assertEqual((e.source, e.edition), ("SRD 5.2.1", "2024"))
                self.assertTrue(e.section)
                self.assertGreater(e.page, 0)
                self.assertLessEqual(e.page, 364)  # the SRD has 364 pages
                self.assertRegex(e.citation, r"^SRD 5\.2\.1, [A-Za-z ]+, p\. \d+$")
                self.assertGreater(len(e.text), 40)
                self.assertNotRegex(e.text, r"\w- \w|\w -\w", "a cut word was left in")
                for key in ("casting_time", "range", "components", "duration"):
                    if e.kind == "spell":
                        self.assertTrue(e.details[key], f"{e.name}: {key}")

    def test_spells_sit_in_the_spell_pages_and_conditions_in_the_glossary(self) -> None:
        self.assertTrue(all(107 <= e.page <= 175 for e in self.spells))
        self.assertTrue(all(176 <= e.page <= 191 for e in self.conditions))

    def test_the_documents_slips_are_read_as_meant(self) -> None:
        names = {e.name for e in self.spells}
        self.assertIn("Acid Splash", names)
        self.assertNotIn("Acid SplASh", names)
        barkskin = self.srd.lookup("Barkskin", "2024", kind="spell")
        assert barkskin is not None
        self.assertEqual(barkskin.entry.details["components"], "V, S, M (a handful of bark)")

    def test_a_spell_with_a_table_keeps_it(self) -> None:
        hit = self.srd.lookup("Confusion", "2024", kind="spell")
        assert hit is not None
        self.assertIn("1d10 Behavior for the Turn", hit.entry.text)
        self.assertIn("The target chooses its behavior.", hit.entry.text)


class OlderNames(unittest.TestCase):
    def test_every_current_name_is_in_the_data(self) -> None:
        names = {normalize(e.name) for e in srd_entries("2024") if e.kind == "spell"}
        for older, current in aliases.SPELL_ALIASES:
            with self.subTest(older):
                self.assertIn(normalize(current), names)
                self.assertNotEqual(normalize(older), normalize(current))
                self.assertNotIn(normalize(older), names)  # it would never be reached

    def test_an_older_name_finds_the_2024_entry(self) -> None:
        for older, current in aliases.SPELL_ALIASES:
            with self.subTest(older):
                hit = index.srd().lookup(older, "2024", "2014", kind="spell")
                assert hit is not None
                self.assertEqual((hit.entry.name, hit.tag, hit.renamed), (current, "", True))
                self.assertEqual(hit.entry.edition, "2024")

    def test_typed_the_way_it_is_said_aloud(self) -> None:
        for said in ("tashas hideous laughter", "Tasha's Hideous Laughter", "bigbys hand"):
            hit = index.srd().lookup(said, "2024", "2014")
            self.assertIsNotNone(hit, said)
        hit = index.srd().lookup("mordenkainens sword", "2024")
        assert hit is not None
        self.assertEqual(hit.entry.name, "Arcane Sword")

    def test_the_list_has_no_repeats(self) -> None:
        olds = [normalize(o) for o, _ in aliases.SPELL_ALIASES]
        self.assertEqual(len(olds), len(set(olds)))

    def test_the_two_reworked_renames_find_the_2024_spell(self) -> None:
        # Renamed in 2024 along with a rewrite: still the newer version of the same spell,
        # so it must not be answered with the 2014 spell of the old name.
        for older, current in (("Feeblemind", "Befuddlement"), ("Branding Smite", "Shining Smite")):
            hit = index.srd().lookup(older, "2024", "2014", kind="spell")
            assert hit is not None
            self.assertEqual((hit.entry.name, hit.tag, hit.renamed), (current, "", True))
            self.assertIn((older, current), aliases.SPELL_ALIASES)

    def test_a_name_in_neither_edition_is_a_miss_even_with_a_fallback(self) -> None:
        # Neither edition has it, so the fallback finds nothing: a miss, not a guess.
        self.assertIsNone(index.srd().lookup("Sleet Storm of Ruin", "2024", "2014"))


class TheSrdOnly(unittest.TestCase):
    """Nothing hand-added: spells from other books must never appear here."""

    NOT_IN_THE_SRD = (
        "Armor of Agathys", "Hunger of Hadar", "Toll the Dead", "Booming Blade",
        "Mind Sliver", "Tasha’s Bubbling Cauldron", "Green-Flame Blade", "Sword Burst",
        "Healing Spirit", "Create Bonfire", "Word of Radiance",
    )  # fmt: skip

    def test_spells_from_other_books_are_absent(self) -> None:
        for name in self.NOT_IN_THE_SRD:
            with self.subTest(name):
                self.assertIsNone(index.srd().lookup(name, "2024", "2014"))

    def test_no_title_carries_a_creators_name(self) -> None:
        # The possessives that are left are not people (a job, a dragon, a hunter): a title
        # like "Melf's Acid Arrow" in the data would show up here as an extra.
        possessive = {e.name for e in srd_entries("2024") if "’s " in e.name}
        self.assertEqual(possessive, {"Arcanist’s Magic Aura", "Dragon’s Breath", "Hunter’s Mark"})

    def test_every_entry_is_read_only_however_it_was_made(self) -> None:
        details = {"level": 2}
        made = Entry(
            "spell", "Test", "2024", "SRD 5.2.1", "Spell Descriptions", 1, "words", details
        )
        details["level"] = 9  # the caller's own dict is a separate thing
        self.assertEqual(made.details["level"], 2)
        with self.assertRaises(TypeError):
            made.details["level"] = 3  # type: ignore[index]

    def test_stat_blocks_are_one_line_to_an_entry(self) -> None:
        labels = re.compile(
            r"^(AC|HP|Speed|Initiative|MOD SAVE|Str|Int|Skills|Resistances|Vulnerabilities"
            r"|Immunities|Senses|Languages|CR|Gear) "
        )
        headings = re.compile(r"^(Traits|Actions|Bonus Actions|Reactions)$")
        entry = re.compile(
            r"^[A-Z][A-Za-z’' ]*(\([^)]*\))?\. "
        )  # "Slam. ", "Fey Step (Fey Only). "
        found = 0
        for e in srd_entries("2024"):
            lines = e.text.splitlines()
            if "MOD SAVE MOD SAVE MOD SAVE" not in lines:
                continue
            found += 1
            start = next(i for i, ln in enumerate(lines) if ln.startswith("AC "))
            for ln in lines[start:]:
                with self.subTest(e.name, line=ln[:50]):
                    self.assertTrue(labels.match(ln) or headings.match(ln) or entry.match(ln))
        self.assertEqual(found, 4)

    def test_names_inside_a_sentence_and_table_columns_read_as_written(self) -> None:
        by_name = {e.name: e.text for e in srd_entries("2024")}
        self.assertIn(
            "This creature uses the Otherworldly Steed stat block.", by_name["Find Steed"]
        )
        self.assertIn("The target must be either a Beast or Plant creature", by_name["Awaken"])
        self.assertIn("the Awakened Shrub or Awakened Tree", by_name["Awaken"])
        self.assertIn("over six Ghouls", by_name["Create Undead"])
        weather = by_name["Control Weather"].splitlines()
        self.assertIn("Temperature Wind", weather)
        self.assertIn("Stage Condition Stage Condition", weather)
        self.assertNotRegex(by_name["Control Weather"], r"[a-ce-z]\d |\d[A-Z]")  # kept apart
        self.assertIn("Bat, Cat, Frog, Hawk, Lizard, Octopus", by_name["Find Familiar"])
        # The tool puts no space before a full stop. (A few "name ." spaces are in the PDF
        # itself, drawn as a piece that ends in a space; they are kept as the PDF has them.)
        self.assertNotIn("Ghouls .", by_name["Create Undead"])
        self.assertIn("not even Wish—can", by_name["Mind Blank"])
        self.assertIn("Familiarity Mishap Area Target Target", by_name["Teleport"])

    def test_entry_details_cannot_be_changed_in_place(self) -> None:
        hit = index.srd().lookup("Fireball", "2024", kind="spell")
        assert hit is not None
        with self.assertRaises(TypeError):
            hit.entry.details["level"] = 9  # type: ignore[index]
        self.assertEqual(hit.entry.details["level"], 3)
        self.assertIsInstance(hash(hit.entry), int)  # still usable in sets

    def test_the_text_is_the_srds_not_a_garbled_copy(self) -> None:
        by_name = {e.name: e.text for e in srd_entries("2024")}
        self.assertIn("ten 10-foot-by-10-foot panels", by_name["Wall of Stone"])
        for name in ("Animate Objects", "Find Steed", "Giant Insect", "Summon Dragon"):
            text = by_name[name]
            with self.subTest(name):
                self.assertRegex(text, r"\nStr \d+ [+−]\d+ [+−]\d+ Dex \d+ .* Con \d+ ")
                self.assertRegex(text, r"\nInt \d+ .* Wis \d+ .* Cha \d+ ")
                self.assertNotRegex(text, r"\b(dex|con|WiS|chA|int) \d")  # small-capital slips
        stat = by_name["Find Steed"]
        self.assertIn(
            "(the steed has a number of Hit Dice [d10s] equal to the spell’s level)", stat
        )
        for name, text in by_name.items():
            with self.subTest(name):
                self.assertNotRegex(text, r"[a-z]\d+-(foot|minute|hour|day)", name)  # "by10-foot"


class Provenance(unittest.TestCase):
    """Only the SRD, and the licence's conditions met."""

    FOLDERS = (("srd52", "SRD 5.2.1", ATTRIBUTION), ("srd51", "SRD 5.1", ATTRIBUTION_51))

    def test_the_data_folder_holds_only_the_srd_files(self) -> None:
        names = sorted(p.relative_to(DATA).as_posix() for p in DATA.rglob("*") if p.is_file())
        expected = [
            f"{d}/{n}" for d, files in (("srd51", FILES), ("srd52", FILES_52)) for n in files
        ]
        self.assertEqual(names, expected)
        self.assertEqual(sorted(p.name for p in DATA.iterdir()), ["srd51", "srd52"])

    def test_each_attribution_is_the_one_its_srd_asks_for(self) -> None:
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        quoted = re.sub(r"^> ?", "", readme, flags=re.MULTILINE).replace("<", "").replace(">", "")
        for folder, _source, statement in self.FOLDERS:
            with self.subTest(folder):
                text = (DATA / folder / "ATTRIBUTION.md").read_text(encoding="utf-8")
                self.assertIn(statement, text)
                self.assertIn("Changes made", text)  # CC BY 4.0: say what was changed
                self.assertIn(" ".join(statement.split()), " ".join(quoted.split()))

    def test_each_file_says_where_it_came_from(self) -> None:
        for folder, source, _statement in self.FOLDERS:
            documents = []
            for name in ("spells.json", "conditions.json"):
                data = json.loads((DATA / folder / name).read_text(encoding="utf-8"))
                documents.append(data["document"])
                self.assertEqual((data["source"], data["licence"]), (source, "CC-BY-4.0"))
                self.assertTrue(data["document"]["url"].startswith("https://media.dndbeyond.com/"))
                self.assertRegex(data["document"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(documents[0], documents[1], folder)  # made from one file

    def test_the_files_keep_the_tools_layout(self) -> None:
        # Only the layout: a hand edit of a spell's words would still pass. The real check
        # is running the tool on the PDF and seeing no change (ATTRIBUTION.md).
        for folder in ("srd52", "srd51"):
            for name in ("spells.json", "conditions.json", "monsters.json"):
                text = (DATA / folder / name).read_text(encoding="utf-8")
                self.assertEqual(build.dump(json.loads(text)), text, f"{folder}/{name}")

    def test_the_pdf_is_not_kept_in_the_repository(self) -> None:
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "*.pdf"], cwd=REPO, capture_output=True, text=True, check=True
            ).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git isn't available here")
        self.assertEqual(tracked, [])

    def test_every_data_file_is_in_the_package(self) -> None:
        # The Docker image does a normal install: a file no package-data pattern matches
        # would be missing there, and the lookups would find nothing.
        package = REPO / "core" / "src" / "dmbot"
        toml = tomllib.loads((REPO / "core" / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = toml["tool"]["setuptools"]["package-data"]["dmbot"]
        shipped = {path for pattern in patterns for path in package.glob(pattern)}
        files = {path for path in DATA.rglob("*") if path.is_file()}
        self.assertTrue(files)
        self.assertEqual(files - shipped, set())

    def test_a_hit_knows_its_entry(self) -> None:
        hit = Hit(spell("Fireball"), "", "fireball")
        self.assertFalse(hit.renamed)


class TheLegacyData(unittest.TestCase):
    """The 2014 SRD 5.1, as the legacy fallback (#873)."""

    spells: list[Entry]
    conditions: list[Entry]

    @classmethod
    def setUpClass(cls) -> None:
        cls.spells = [e for e in srd_entries("2014") if e.kind == "spell"]
        cls.conditions = [e for e in srd_entries("2014") if e.kind == "condition"]

    def test_every_spell_and_condition_of_the_srd_is_there(self) -> None:
        self.assertEqual((len(self.spells), len(self.conditions)), (319, 15))
        self.assertEqual(len({normalize(e.name) for e in self.spells}), 319)
        newer = {e.name for e in srd_entries("2024") if e.kind == "condition"}
        self.assertEqual({e.name for e in self.conditions}, newer)  # the same fifteen

    def test_every_entry_is_cited_and_clean(self) -> None:
        for e in srd_entries("2014"):
            with self.subTest(e.name):
                self.assertEqual((e.source, e.edition), ("SRD 5.1", "2014"))
                self.assertGreater(e.page, 0)
                self.assertLessEqual(e.page, 403)  # the SRD 5.1 has 403 pages
                self.assertRegex(e.citation, r"^SRD 5\.1, [A-Za-z :-]+, p\. \d+$")
                self.assertGreater(len(e.text), 40)
                self.assertNotRegex(e.text, r"\w- \w|\w -\w|[\t\xa0\xad]", "noise was left in")
                if e.kind == "spell":
                    self.assertIn(e.details["level"], range(10))
                    for key in ("school", "casting_time", "range", "components", "duration"):
                        self.assertTrue(e.details[key], key)

    def test_a_2014_spell_is_read_whole(self) -> None:
        hit = index.srd().lookup("Sleet Storm", "2014", kind="spell")
        assert hit is not None
        self.assertEqual(hit.tag, "[Legacy 2014]")
        self.assertEqual(hit.entry.details["level"], 3)
        self.assertEqual(hit.entry.details["school"], "Conjuration")
        self.assertIn("20-foot-tall cylinder", hit.entry.text)
        self.assertTrue(hit.citation.endswith("[Legacy 2014]"))

    def test_the_newer_spell_wins_and_an_old_name_still_finds_it(self) -> None:
        for name in ("Fireball", "Melf's Acid Arrow", "Feeblemind"):
            hit = index.srd().lookup(name, "2024", "2014", kind="spell")
            assert hit is not None
            self.assertEqual((hit.entry.edition, hit.tag), ("2024", ""), name)
        # the two spells the 2024 rules renamed are found under their old names, as the new ones
        self.assertEqual(
            {e.name for e in self.spells} - {e.name for e in srd_entries("2024")},
            {"Feeblemind", "Branding Smite"},
        )
        # a 2014 campaign gets the 2014 spell, tagged, and the fallback is the 2024 one
        old = index.srd().lookup("Fireball", "2014", "2024", kind="spell")
        assert old is not None
        self.assertEqual((old.entry.edition, old.tag), ("2014", "[Legacy 2014]"))

    def test_a_cantrip_and_a_ritual_are_read(self) -> None:
        by_name = {e.name: e for e in self.spells}
        self.assertEqual(by_name["Acid Splash"].details["level"], 0)
        self.assertEqual(by_name["Detect Magic"].details["level"], 1)
        self.assertIs(by_name["Detect Magic"].details["ritual"], True)
        self.assertIs(by_name["Fireball"].details["ritual"], False)

    def test_the_files_were_made_with_the_current_5_2_1_words(self) -> None:
        # The 5.1 reading uses the 5.2.1 data's words: if that data changes, make these again.
        for name in ("spells.json", "conditions.json"):
            data = json.loads((DATA / "srd51" / name).read_text(encoding="utf-8"))
            self.assertEqual(data["document"]["word_list_sha256"], build.word_list_sha256(), name)

    def test_only_the_listed_defects_remain(self) -> None:
        # The PDF lost a few words; ATTRIBUTION.md lists what is left, and this fails if a
        # worse reading slips in or a listed defect goes away (then edit both).
        text = {e.name: all_text(e) for e in srd_entries("2014")}
        left = {name: parse51.strays([t]) for name, t in text.items() if parse51.strays([t])}
        self.assertEqual(left, {"Animal Friendship": {"t": 1}})
        self.assertIn("beast t level above 1st", text["Animal Friendship"])

    def test_cut_words_are_put_back_and_no_pieces_are_left_beside_each_other(self) -> None:
        # Every pair of neighbouring words, neither of which the 5.2.1 data knows, where
        # one is a short piece: the shape of a cut word. These few are real words that the
        # 5.2.1 data happens not to use.
        words = build.known_words()
        words = {w for w in words if "-" not in w}
        found = set()
        for e in srd_entries("2014"):
            tokens = all_text(e).split()
            for a, b in itertools.pairwise(tokens):
                left, right = re.sub(r"[^A-Za-z’']", "", a), re.sub(r"[^A-Za-z’']", "", b)
                if (
                    left
                    and right
                    and a[-1:].isalpha()
                    and b[:1].isalpha()
                    and left.lower() not in words
                    and right.lower() not in words
                    and min(len(left), len(right)) <= 3
                ):
                    found.add((left.lower(), right.lower()))
        self.assertEqual(
            found,
            {
                ("his", "mace"), ("his", "hammer"), ("her", "warhorse"), ("beggar", "she"),
                ("she", "meets"), ("bed", "linen"), ("dimly", "lit"), ("sealable", "lid"),
                ("leadbased", "ink"), ("nut", "shells"), ("hind", "leg"), ("rotten", "egg"),
                ("few", "grains"), ("sweet", "oil"), ("carved", "bar"), ("bag", "containing"),
                ("gum", "arabic"),
            },
        )  # fmt: skip

    def test_the_repairs_that_went_wrong_once_stay_right(self) -> None:
        # Joined wrongly by an earlier reading (Supervisor review of #894), or never joined.
        wrong = (
            "then ature", "At arget", "then earest", "or as ymbol", "as pecific", "off ood",
            "int he", "int his", "atta cked", "exc ess", "ins tantaneous", "Investigat ion",
            "dre ad", "def ends", "defe nds", "dis eases", "4thlevel",
            "too r less", "forth e", "includin g", "y ou", "l ips", "reli quary", "3 d10", "st rip",
            "gumarabic", "rottenegg", "sealablelid", "nutshells", "foxden", "hindleg",
        )  # fmt: skip
        for e in srd_entries("2014"):
            for bad in wrong:
                with self.subTest(e.name, bad=bad):
                    self.assertIsNone(re.search(rf"(?<!\w){re.escape(bad)}(?!\w)", all_text(e)))
        by_name = {e.name: e.text for e in srd_entries("2014")}
        self.assertIn("the nature", by_name["Animal Shapes"])
        self.assertIn("A target takes 8d6", by_name["Circle of Death"])
        self.assertIn("equal to or less", by_name["Dispel Magic"])
        self.assertIn("for the duration", by_name["Flesh to Stone"])
        # the PDF itself has the hyphen here, in the middle of the line
        self.assertIn("by the thunder-wave spell", by_name["Grappled"])
        for name, text in by_name.items():
            self.assertNotRegex(text, r"\w [.,;:](\s|$)", name)  # a space before a stop


if __name__ == "__main__":
    unittest.main()
