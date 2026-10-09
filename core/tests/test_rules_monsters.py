"""The shipped monster data (#900): the SRD 5.2.1's 330 stat blocks and the SRD 5.1's 317 as
the legacy fallback: complete, cited, clean, found by name, and found by their older names."""

from __future__ import annotations

import itertools
import json
import math
import re
import unittest
from fractions import Fraction

from dmbot.devtools.srd import build, parse51
from dmbot.rules import aliases, index
from dmbot.rules.index import DATA, Entry, monster_keys, normalize

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


def monsters(edition: str) -> list[Entry]:
    return [e for e in index.srd().entries if e.kind == "monster" and e.edition == edition]


def all_text(entry: Entry) -> str:
    """Everything a stat block says in words."""
    values = [entry.text, *(v for v in entry.details.values() if isinstance(v, str))]
    return "\n".join(values)


class Both(unittest.TestCase):
    """What every stat block of either edition must satisfy."""

    def test_the_hit_points_are_the_average_of_the_hit_dice(self) -> None:
        for e in monsters("2024") + monsters("2014"):
            dice = re.fullmatch(r"(\d+)d(\d+)(?: *([+−-]) *(\d+))?", str(e.details["hit_dice"]))
            with self.subTest(e.name, edition=e.edition):
                assert dice is not None
                count, sides = int(dice[1]), int(dice[2])
                bonus = int(dice[4] or 0) * (-1 if dice[3] in ("-", "−") else 1)
                self.assertEqual(e.details["hp"], count * (sides + 1) // 2 + bonus)

    def test_every_stat_block_has_all_its_fields(self) -> None:
        for e in monsters("2024") + monsters("2014"):
            with self.subTest(e.name, edition=e.edition):
                for key in ("size", "type", "alignment", "speed", "senses", "languages", "cr"):
                    self.assertTrue(e.details[key], key)
                for key in ("ac", "hp", "xp", *ABILITIES):
                    self.assertIsInstance(e.details[key], int, key)
                self.assertTrue(all(1 <= e.details[a] <= 30 for a in ABILITIES))
                self.assertGreater(len(e.text), 40)
                self.assertRegex(e.details["size"], r"^(Tiny|Small|Medium|Large|Huge|Gargantuan)")

    def test_text_has_no_cut_words_stray_spaces_or_stray_letters(self) -> None:
        for e in monsters("2024") + monsters("2014"):
            text = all_text(e)
            with self.subTest(e.name, edition=e.edition):
                self.assertIsNone(re.search(r"\s[.,;:)]|\w- \w|  |[\t\xa0\xad]", text))
                # a lone capital B is a choice, "(A) ... or (B) ..." in the dragons
                left = {k: v for k, v in parse51.strays([text]).items() if k != "B"}
                self.assertEqual(left, {})

    def test_no_name_carries_a_creators_name(self) -> None:
        for e in monsters("2024") + monsters("2014"):
            self.assertNotRegex(e.name, r"’s ", e.name)


class Newer(unittest.TestCase):
    """The SRD 5.2.1's creatures."""

    def test_every_creature_of_the_srd_is_there(self) -> None:
        found = monsters("2024")
        self.assertEqual(len(found), 330)
        self.assertEqual(len({normalize(e.name) for e in found}), 330)  # no repeats
        sections = {e.section for e in found}
        self.assertEqual(sections, {"Monsters A–Z", "Animals"})
        self.assertEqual(sum(e.section == "Animals" for e in found), 95)

    def test_each_is_cited(self) -> None:
        for e in monsters("2024"):
            with self.subTest(e.name):
                self.assertEqual((e.source, e.edition), ("SRD 5.2.1", "2024"))
                self.assertRegex(e.citation, r"^SRD 5\.2\.1, (Monsters A–Z|Animals), p\. \d+$")
                self.assertTrue(258 <= e.page <= 364)  # the SRD has 364 pages

    def test_the_proficiency_bonus_follows_the_challenge_rating(self) -> None:
        for e in monsters("2024"):
            rating = Fraction(str(e.details["cr"]))
            expected = 2 + (max(1, math.ceil(rating)) - 1) // 4
            with self.subTest(e.name):
                self.assertEqual(e.details["pb"], expected)

    def test_a_small_creature_is_read_whole(self) -> None:
        hit = index.srd().lookup("Goblin Warrior", "2024", kind="monster")
        assert hit is not None
        self.assertEqual(
            dict(hit.entry.details),
            {
                "size": "Small", "type": "Fey (Goblinoid)", "alignment": "Chaotic Neutral",
                "ac": 15, "hp": 10, "hit_dice": "3d6", "speed": "30 ft.",
                "str": 8, "dex": 15, "con": 10, "int": 10, "wis": 8, "cha": 8,
                "saves": "", "skills": "Stealth +6", "resistances": "", "vulnerabilities": "",
                "immunities": "", "senses": "Darkvision 60 ft.; Passive Perception 9",
                "languages": "Common, Goblin", "cr": "1/4", "xp": 50,
                "challenge": "1/4 (XP 50; PB +2)", "initiative": "+2 (12)",
                "gear": "Leather Armor, Scimitar, Shield, Shortbow", "pb": 2,
            },
        )  # fmt: skip
        lines = hit.entry.text.split("\n")
        self.assertEqual(lines[0], "Actions")
        self.assertTrue(lines[1].startswith("Scimitar. Melee Attack Roll: +4, reach 5 ft."))
        self.assertIn("Bonus Actions", lines)
        self.assertEqual(hit.citation, "SRD 5.2.1, Monsters A–Z, p. 290")

    def test_a_creature_with_legendary_actions_and_a_lair(self) -> None:
        hit = index.srd().lookup("Aboleth", "2024", kind="monster")
        assert hit is not None
        e = hit.entry
        self.assertEqual(e.details["saves"], "Dex +3, Con +6, Int +8, Wis +6")
        self.assertEqual(e.details["challenge"], "10 (XP 5,900, or 7,200 in lair; PB +4)")
        self.assertEqual(e.details["xp"], 5900)
        lines = e.text.split("\n")
        self.assertEqual([x for x in lines if x in ("Traits", "Actions", "Legendary Actions")],
                         ["Traits", "Actions", "Legendary Actions"])  # fmt: skip
        self.assertIn("Legendary Resistance (3/Day, or 4/Day in Lair). If the aboleth", e.text)

    def test_a_swarm_keeps_its_type(self) -> None:
        hit = index.srd().lookup("Swarm of Insects", "2024", kind="monster")
        assert hit is not None
        self.assertEqual(hit.entry.details["type"], "Swarm of Tiny Beasts")
        self.assertEqual(hit.entry.details["size"], "Medium")
        self.assertEqual(hit.entry.details["languages"], "None")

    def test_a_table_stays_lines(self) -> None:
        hit = index.srd().lookup("Gibbering Mouther", "2024", kind="monster")
        assert hit is not None
        rows = [x for x in hit.entry.text.split("\n") if re.match(r"^\d[–-]\d?\.? ", x)]
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(rows[-1].startswith("7–8."))

    def test_a_dragon_with_a_choice_keeps_it(self) -> None:
        hit = index.srd().lookup("Adult Brass Dragon", "2024", kind="monster")
        assert hit is not None
        self.assertIn("a use of (A) Sleep Breath or (B) Spellcasting to cast", hit.entry.text)

    def test_numbers_and_commas_read_as_printed(self) -> None:
        text = "\n".join(all_text(e) for e in monsters("2024"))
        self.assertNotRegex(text, r"\d ,")  # "+7 , reach" was the PDF's gap
        self.assertIn("(XP 11,500, or 13,000 in lair; PB +5)", text)

    def test_the_words_are_the_srds_not_a_garbled_copy(self) -> None:
        # Every pair of neighbouring words that neither the 5.2.1 spell and condition
        # data nor the stat blocks themselves use elsewhere would be a cut word.
        known = {w for w in build.known_words(monsters_too=True) if "-" not in w}
        seen: dict[str, int] = {}
        for e in monsters("2024"):
            for word in re.findall(r"[A-Za-z’']+", all_text(e)):
                seen[word.lower()] = seen.get(word.lower(), 0) + 1
        for e in monsters("2024"):
            tokens = all_text(e).split()
            for a, b in itertools.pairwise(tokens):
                left = re.sub(r"[^A-Za-z’']", "", a).lower()
                right = re.sub(r"[^A-Za-z’']", "", b).lower()
                if left and right and a[-1:].isalpha() and b[:1].isalpha():
                    with self.subTest(e.name, pair=(left, right)):
                        self.assertFalse(
                            left not in known
                            and right not in known
                            and min(len(left), len(right)) <= 3
                        )


class Older(unittest.TestCase):
    """The 2014 SRD 5.1's creatures, as the legacy fallback."""

    def test_every_creature_of_the_srd_is_there(self) -> None:
        found = monsters("2014")
        self.assertEqual(len(found), 317)
        self.assertEqual(len({normalize(e.name) for e in found}), 317)
        by_section = {s: sum(e.section == s for e in found) for s in {e.section for e in found}}
        self.assertEqual(
            by_section,
            {
                "Monsters": 201,
                "Appendix MM-A: Miscellaneous Creatures": 95,
                "Appendix MM-B: Nonplayer Characters": 21,
            },
        )

    def test_each_is_cited_and_tagged_as_legacy(self) -> None:
        for e in monsters("2014"):
            with self.subTest(e.name):
                self.assertEqual((e.source, e.edition), ("SRD 5.1", "2014"))
                self.assertRegex(e.citation, r"^SRD 5\.1, [A-Za-z :-]+, p\. \d+$")
                self.assertTrue(261 <= e.page <= 403)
        hit = index.srd().lookup("Orc", "2024", "2014", kind="monster")
        assert hit is not None
        self.assertEqual(hit.tag, "[Legacy 2014]")
        self.assertTrue(hit.citation.endswith("[Legacy 2014]"))

    def test_a_small_creature_is_read_whole(self) -> None:
        hit = index.srd().lookup("Goblin", "2014", kind="monster")
        assert hit is not None
        self.assertEqual(
            dict(hit.entry.details),
            {
                "size": "Small", "type": "humanoid (goblinoid)", "alignment": "neutral evil",
                "ac": 15, "hp": 7, "hit_dice": "2d6", "speed": "30 ft.",
                "str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8,
                "saves": "", "skills": "Stealth +6", "resistances": "", "vulnerabilities": "",
                "immunities": "", "senses": "darkvision 60 ft., passive Perception 9",
                "languages": "Common, Goblin", "cr": "1/4", "xp": 50,
                "challenge": "1/4 (50 XP)", "ac_note": "(leather armor, shield)",
                "condition_immunities": "",
            },
        )  # fmt: skip
        lines = hit.entry.text.split("\n")
        self.assertTrue(lines[0].startswith("Nimble Escape. The goblin can take the Disengage"))
        self.assertEqual(lines[1], "Actions")
        self.assertEqual(hit.citation, "SRD 5.1, Monsters, p. 315 [Legacy 2014]")

    def test_a_creature_with_legendary_actions(self) -> None:
        e = next(x for x in monsters("2014") if x.name == "Aboleth")
        self.assertIn("Legendary Actions", e.text.split("\n"))
        self.assertEqual(e.details["saves"], "Con +6, Int +8, Wis +6")
        self.assertEqual(e.details["xp"], 5900)

    def test_an_armor_class_with_a_condition_and_a_type_over_two_lines(self) -> None:
        e = next(x for x in monsters("2014") if x.name == "Werebear")
        self.assertEqual(e.details["ac"], 10)
        self.assertEqual(
            e.details["ac_note"], "in humanoid form, 11 (natural armor) in bear and hybrid form"
        )
        self.assertEqual(e.details["type"], "humanoid (human, shapechanger)")
        self.assertEqual(e.details["alignment"], "neutral good")

    def test_a_spell_list_is_a_line_for_each_level(self) -> None:
        e = next(x for x in monsters("2014") if x.name == "Archmage")
        lines = e.text.split("\n")
        self.assertIn("2nd level (3 slots): detect thoughts, mirror image, misty step", lines)
        self.assertTrue(any(x.startswith("Cantrips (at will): fire bolt") for x in lines))

    def test_the_book_s_prose_and_group_names_are_not_in_a_stat_block(self) -> None:
        for e in monsters("2014"):
            with self.subTest(e.name):
                last = e.text.split("\n")[-1]
                self.assertTrue(last.endswith((".", ")", "*")), last[-60:])
        text = "\n".join(e.text for e in monsters("2014"))
        self.assertNotIn("Archmages are powerful", text)

    def test_a_split_number_is_put_back(self) -> None:
        e = next(x for x in monsters("2014") if x.name == "Horned Devil")
        self.assertEqual(e.details["hit_dice"], "17d10 + 85")
        rex = next(x for x in monsters("2014") if x.name == "Tyrannosaurus Rex")
        self.assertIn("Weapon Attack: +10 to hit", rex.text)

    def test_the_words_are_the_srds_not_a_garbled_copy(self) -> None:
        known = {w for w in build.known_words(monsters_too=True) if "-" not in w}
        allowed: set[tuple[str, str]] = set()  # real words the 5.2.1 data happens not to use
        found = set()
        for e in monsters("2014"):
            tokens = all_text(e).split()
            for a, b in itertools.pairwise(tokens):
                left = re.sub(r"[^A-Za-z’']", "", a).lower()
                right = re.sub(r"[^A-Za-z’']", "", b).lower()
                if (
                    left
                    and right
                    and a[-1:].isalpha()
                    and b[:1].isalpha()
                    and left not in known
                    and right not in known
                    and min(len(left), len(right)) <= 3
                ):
                    found.add((left, right))
        self.assertEqual(found, allowed)


class Names(unittest.TestCase):
    def test_every_current_name_is_in_the_data(self) -> None:
        names = {normalize(e.name) for e in monsters("2024")}
        for older, current in aliases.MONSTER_ALIASES:
            with self.subTest(older):
                self.assertIn(normalize(current), names)
                self.assertNotEqual(normalize(older), normalize(current))
                self.assertNotIn(normalize(older), names)  # it would never be reached

    def test_the_list_has_no_repeats_and_every_older_name_is_a_5_1_creature(self) -> None:
        olds = [normalize(o) for o, _ in aliases.MONSTER_ALIASES]
        self.assertEqual(len(olds), len(set(olds)))
        older = {normalize(e.name) for e in monsters("2014")}
        for name in olds:
            self.assertIn(name, older)

    def test_the_names_without_a_5_2_1_creature_are_exactly_the_aliased_ones_and_seven(
        self,
    ) -> None:
        newer = {normalize(e.name) for e in monsters("2024")}
        missing = {normalize(e.name) for e in monsters("2014")} - newer
        aliased = {normalize(o) for o, _ in aliases.MONSTER_ALIASES}
        self.assertEqual(
            {e.name for e in monsters("2014") if normalize(e.name) in missing - aliased},
            {
                "Duergar", "Elf, Drow", "Gnome, Deep (Svirfneblin)", "Half-Red Dragon Veteran",
                "Lizardfolk", "Orc", "Succubus/Incubus",
            },
        )  # fmt: skip

    def test_an_older_name_finds_the_newer_creature(self) -> None:
        for older, current in aliases.MONSTER_ALIASES:
            with self.subTest(older):
                hit = index.srd().lookup(older, "2024", "2014", kind="monster")
                assert hit is not None
                self.assertEqual((hit.entry.name, hit.tag, hit.renamed), (current, "", True))
                self.assertEqual(hit.entry.edition, "2024")

    def test_a_name_with_legacy_or_a_bracket_is_found_by_its_plain_name(self) -> None:
        srd = index.srd()
        for said in ("Goblin Warrior (Legacy)", "goblin warrior (2024)", "Aboleth (Legacy)"):
            hit = srd.lookup(said, "2024", "2014", kind="monster")
            self.assertIsNotNone(hit, said)
        goblin = srd.lookup("Goblin (Legacy)", "2024", "2014", kind="monster")
        assert goblin is not None
        self.assertEqual(goblin.entry.name, "Goblin Warrior")  # the newer one wins

    def test_a_2014_campaign_gets_the_2014_creature_first(self) -> None:
        hit = index.srd().lookup("Goblin", "2014", "2024", kind="monster")
        assert hit is not None
        self.assertEqual((hit.entry.edition, hit.tag), ("2014", "[Legacy 2014]"))
        hit = index.srd().lookup("Goblin Warrior", "2014", "2024", kind="monster")
        assert hit is not None
        self.assertEqual((hit.entry.edition, hit.tag), ("2024", "[2024]"))

    def test_a_creature_only_the_2014_book_has_is_legacy(self) -> None:
        for said in ("Duergar", "Drow Elf", "Elf, Drow", "Deep Gnome", "Svirfneblin", "Lizardfolk"):
            hit = index.srd().lookup(said, "2024", "2014", kind="monster")
            with self.subTest(said):
                assert hit is not None
                self.assertEqual((hit.entry.edition, hit.tag), ("2014", "[Legacy 2014]"))
        self.assertIsNone(index.srd().lookup("Duergar", "2024", kind="monster"))

    def test_a_creature_is_not_a_spell(self) -> None:
        self.assertIsNone(index.srd().lookup("Aboleth", "2024", "2014", kind="spell"))
        self.assertIsNone(index.srd().lookup("Fireball", "2024", "2014", kind="monster"))

    def test_other_names_of_a_creature(self) -> None:
        self.assertEqual(monster_keys("Goblin Warrior"), [])
        self.assertEqual(
            monster_keys("Gnome, Deep (Svirfneblin)"), ["gnome deep", "svirfneblin", "deep gnome"]
        )
        self.assertEqual(monster_keys("Elf, Drow"), ["drow elf"])
        self.assertEqual(monster_keys("Succubus/Incubus"), ["succubus", "incubus"])

    def test_a_2014_campaign_finds_each_side_of_succubus_incubus(self) -> None:
        for said in ("Succubus", "Incubus"):
            hit = index.srd().lookup(said, "2014", "2024", kind="monster")
            with self.subTest(said):
                assert hit is not None
                self.assertEqual(hit.entry.name, "Succubus/Incubus")
        newer = index.srd().lookup("Succubus", "2024", "2014", kind="monster")
        assert newer is not None
        self.assertEqual((newer.entry.name, newer.entry.edition), ("Succubus", "2024"))

    def test_the_bracket_rule_holds_for_spells_too(self) -> None:
        hit = index.srd().lookup("Fireball (Legacy)", "2024", "2014", kind="spell")
        assert hit is not None
        self.assertEqual((hit.entry.name, hit.entry.edition), ("Fireball", "2024"))


class Provenance(unittest.TestCase):
    def test_only_the_srd_is_in_the_data(self) -> None:
        for folder in ("srd51", "srd52"):
            data = json.loads((DATA / folder / "monsters.json").read_text(encoding="utf-8"))
            self.assertEqual(data["kind"], "monster")
            self.assertEqual(data["licence"], "CC-BY-4.0")
            self.assertNotIn("section", data)  # each entry names its own
            self.assertRegex(data["document"]["sha256"], r"^[0-9a-f]{64}$")
            if folder == "srd51":
                self.assertRegex(data["document"]["word_list_sha256"], r"^[0-9a-f]{64}$")
            else:  # the 5.2.1 is built from its PDF alone
                self.assertNotIn("word_list_sha256", data["document"])

    def test_the_5_1_monsters_were_made_with_the_current_5_2_1_words(self) -> None:
        data = json.loads((DATA / "srd51" / "monsters.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data["document"]["word_list_sha256"], build.word_list_sha256(monsters_too=True)
        )

    def test_the_files_keep_the_tools_layout(self) -> None:
        for folder in ("srd52", "srd51"):
            text = (DATA / folder / "monsters.json").read_text(encoding="utf-8")
            self.assertEqual(build.dump(json.loads(text)), text, folder)


if __name__ == "__main__":
    unittest.main()
