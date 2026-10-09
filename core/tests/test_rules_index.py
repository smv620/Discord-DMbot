"""The rules index (#866): names, aliases and the order of rulesets, on made-up data; and
the shipped SRD 5.2.1 data: complete, cited, attributed, and nothing from outside it."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

from dmbot.campaigns.models import FALLBACK_NONE
from dmbot.devtools.srd import build
from dmbot.rules import aliases, index
from dmbot.rules.index import DATA, Entry, Hit, Index, edition_tag, normalize

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


class TheShippedData(unittest.TestCase):
    """What DMbot actually ships, as `srd()` loads it."""

    srd: Index
    spells: list[Entry]
    conditions: list[Entry]

    @classmethod
    def setUpClass(cls) -> None:
        cls.srd = index.srd()
        cls.spells = [e for e in cls.srd.entries if e.kind == "spell"]
        cls.conditions = [e for e in cls.srd.entries if e.kind == "condition"]

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
        for e in self.srd.entries:
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
        names = {normalize(e.name) for e in index.srd().entries if e.kind == "spell"}
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
        # so once the 2014 data is added it must not be answered with the old one.
        for older, current in (("Feeblemind", "Befuddlement"), ("Branding Smite", "Shining Smite")):
            hit = index.srd().lookup(older, "2024", "2014", kind="spell")
            assert hit is not None
            self.assertEqual((hit.entry.name, hit.tag, hit.renamed), (current, "", True))
            self.assertIn((older, current), aliases.SPELL_ALIASES)

    def test_a_name_in_neither_edition_is_a_miss_even_with_a_fallback(self) -> None:
        # No 2014 data is shipped yet, so the fallback has nothing: a miss, not a guess.
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
        possessive = {e.name for e in index.srd().entries if "’s " in e.name}
        self.assertEqual(possessive, {"Arcanist’s Magic Aura", "Dragon’s Breath", "Hunter’s Mark"})

    def test_entry_details_cannot_be_changed_in_place(self) -> None:
        hit = index.srd().lookup("Fireball", "2024", kind="spell")
        assert hit is not None
        with self.assertRaises(TypeError):
            hit.entry.details["level"] = 9  # type: ignore[index]
        self.assertEqual(hit.entry.details["level"], 3)
        self.assertIsInstance(hash(hit.entry), int)  # still usable in sets

    def test_the_text_is_the_srds_not_a_garbled_copy(self) -> None:
        by_name = {e.name: e.text for e in index.srd().entries}
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

    def test_the_data_folder_holds_only_the_srd_files(self) -> None:
        names = sorted(p.name for p in DATA.rglob("*") if p.is_file())
        self.assertEqual(names, ["ATTRIBUTION.md", "conditions.json", "spells.json"])
        self.assertEqual([p.name for p in DATA.iterdir()], ["srd52"])

    def test_the_attribution_is_the_one_the_srd_asks_for(self) -> None:
        text = (DATA / "srd52" / "ATTRIBUTION.md").read_text(encoding="utf-8")
        self.assertIn(ATTRIBUTION, text)
        self.assertIn("Changes made", text)  # CC BY 4.0: say what was changed
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        quoted = re.sub(r"^> ?", "", readme, flags=re.MULTILINE).replace("<", "").replace(">", "")
        self.assertIn(" ".join(ATTRIBUTION.split()), " ".join(quoted.split()))

    def test_each_file_says_where_it_came_from(self) -> None:
        documents = []
        for name in ("spells.json", "conditions.json"):
            data = json.loads((DATA / "srd52" / name).read_text(encoding="utf-8"))
            documents.append(data["document"])
            self.assertEqual((data["source"], data["licence"]), ("SRD 5.2.1", "CC-BY-4.0"))
            self.assertTrue(data["document"]["url"].startswith("https://media.dndbeyond.com/"))
            self.assertRegex(data["document"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(documents[0], documents[1])  # made from one file

    def test_the_files_keep_the_tools_layout(self) -> None:
        # Only the layout: a hand edit of a spell's words would still pass. The real check
        # is running the tool on the PDF and seeing no change (ATTRIBUTION.md).
        for name in ("spells.json", "conditions.json"):
            path = DATA / "srd52" / name
            text = path.read_text(encoding="utf-8")
            self.assertEqual(build.dump(json.loads(text)), text, name)

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


if __name__ == "__main__":
    unittest.main()
