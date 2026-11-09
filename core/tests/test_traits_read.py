"""A character's lineage and class from lore (#2959 item 3): the TRAITS lines are checked against
the text, a class is one the rules name, a lineage is found in the rules or kept as said, an
existing name is left alone, and they are written with the name in one change. Invented names."""

from __future__ import annotations

import unittest
from collections import Counter
from types import SimpleNamespace as E
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import lore_read, traits_read
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CLASS, PROPOSED, SPECIES, NewName, RuleLink, Trait
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.ui.name_card import traits_line
from tests.pg import DatabaseTest

TEXT = (
    "Torvin Ashgrove is a dwarf and a cleric of Lathander. His friend Mira Vell is a half-elf "
    "paladin. Old Brannoc, a scholar, lives in the tower. The captain's first mate, an elf, "
    "sails with Captain Rell Dorn."
)
NAMES = ["Torvin Ashgrove", "Mira Vell", "Old Brannoc", "Captain Rell Dorn"]


def found_by_rules(words: str) -> tuple[str, str, str | None] | None:
    return {"dwarf": ("Dwarf", "SRD 5.2.1", "2024")}.get(words.casefold())


def read(
    *lines: str, existing: tuple[str, ...] = ()
) -> tuple[list[traits_read.Found], Counter[str]]:
    dropped: Counter[str] = Counter()
    got = traits_read.read_traits(lines, TEXT, NAMES, found_by_rules, dropped, existing=existing)
    return got, dropped


class TheLines(unittest.TestCase):
    def test_a_lineage_the_rules_have_is_known(self) -> None:
        (got,), _ = read(
            '- Torvin Ashgrove | lineage | dwarf | quote: "Torvin Ashgrove is a dwarf"'
        )
        self.assertEqual(got.trait, Trait(SPECIES, "Dwarf", "SRD 5.2.1", "2024", True))

    def test_a_lineage_the_rules_lack_keeps_the_text_s_words(self) -> None:
        (got,), _ = read('- Mira Vell | lineage | half-elf | quote: "Mira Vell is a half-elf"')
        self.assertEqual(got.trait, Trait(SPECIES, "Half-elf", "", None, False))

    def test_a_class_is_one_the_rules_name_and_a_god_is_not_one(self) -> None:
        (got,), _ = read(
            "- Torvin Ashgrove | class | cleric of Lathander | quote: "
            '"Torvin Ashgrove is a dwarf and a cleric of Lathander"'
        )
        self.assertEqual(got.trait, Trait(CLASS, "Cleric", "SRD 5.2.1", "2024", True))
        none, dropped = read('- Old Brannoc | class | scholar | quote: "Old Brannoc, a scholar"')
        self.assertEqual((none, dropped["trait_not_a_class"]), ([], 1))

    def test_a_half_elf_paladin_is_two_lines(self) -> None:
        got, _ = read(
            '- Mira Vell | lineage | half-elf | quote: "Mira Vell is a half-elf paladin"',
            '- Mira Vell | class | paladin | quote: "Mira Vell is a half-elf paladin"',
        )
        self.assertEqual([g.trait.kind for g in got], [SPECIES, CLASS])

    def test_nothing_the_text_doesn_t_say(self) -> None:
        for line, reason in (
            ('- Torvin Ashgrove | lineage | elf | quote: "Torvin Ashgrove is an elf"',
             "trait_not_in_text"),
            ('- Torvin Ashgrove | lineage | dwarf | quote: "Mira Vell is a half-elf"',
             "trait_not_in_text"),
            ('- Kell Dunmore | lineage | dwarf | quote: "is a dwarf"', "trait_no_name"),
            ('- Torvin Ashgrove | homeland | the hills | quote: "a dwarf"', "trait_kind"),
            ("- Torvin Ashgrove is a dwarf", "trait_unreadable"),
        ):  # fmt: skip
            with self.subTest(line):
                got, dropped = read(line)
                self.assertEqual((got, dropped[reason]), ([], 1))

    def test_a_title_alone_never_ties_someone_else_s_lineage(self) -> None:
        got, dropped = read(
            '- Captain Rell Dorn | lineage | elf | quote: "The captain\'s first mate, an elf"'
        )
        self.assertEqual((got, dropped["trait_not_in_text"]), ([], 1))

    def test_a_lineage_and_class_written_as_one_is_no_lineage(self) -> None:
        got, dropped = read(
            '- Mira Vell | lineage | half-elf paladin | quote: "Mira Vell is a half-elf paladin"'
        )
        self.assertEqual((got, dropped["trait_kind"]), ([], 1))

    def test_an_existing_name_is_left_alone_and_counted(self) -> None:
        got, dropped = read(
            '- Torvin Ashgrove | lineage | dwarf | quote: "Torvin Ashgrove is a dwarf"',
            existing=("torvin ashgrove",),
        )
        self.assertEqual((got, dropped["trait_existing_name"]), ([], 1))

    def test_a_long_phrase_is_no_lineage(self) -> None:
        got, dropped = read(
            '- Old Brannoc | lineage | a scholar, lives in the tower | quote: "Old Brannoc, a '
            'scholar, lives in the tower"'
        )
        self.assertEqual((got, dropped["trait_kind"]), ([], 1))

    def test_the_real_rules_find_a_lineage_newest_first(self) -> None:
        resolve = traits_read.rules_resolver("2024", "2014")
        hit = resolve("dwarf")
        assert hit is not None
        self.assertEqual(hit[0].casefold(), "dwarf")


class TheCampaignsHomebrew(unittest.TestCase):
    """#3926: CLAUDE.md's order, the campaign's homebrew before the rules DMbot ships."""

    def test_a_homebrew_lineage_is_known_as_homebrew_with_no_edition(self) -> None:
        own = traits_read.homebrew_lineages([E(kind="species", name="Half-elf")])  # type: ignore[list-item]
        resolve = traits_read.rules_resolver("2024", "2014", own)
        self.assertEqual(resolve("half-elf"), ("Half-elf", "homebrew", None))
        dropped: Counter[str] = Counter()
        (got,) = traits_read.read_traits(
            ['- Mira Vell | lineage | half-elf | quote: "Mira Vell is a half-elf"'],
            TEXT, NAMES, resolve, dropped,
        )  # fmt: skip
        self.assertEqual(got.trait, Trait(SPECIES, "Half-elf", "homebrew", None, True))

    def test_homebrew_comes_before_the_rules(self) -> None:
        own = traits_read.homebrew_lineages([E(kind="species", name="Dwarf")])  # type: ignore[list-item]
        resolve = traits_read.rules_resolver("2024", "2014", own)
        self.assertEqual(resolve("dwarf"), ("Dwarf", "homebrew", None))

    def test_without_homebrew_the_rules_answer_as_before(self) -> None:
        plain = traits_read.rules_resolver("2024", "2014")
        self.assertEqual(traits_read.rules_resolver("2024", "2014", {})("dwarf"), plain("dwarf"))
        hit = plain("dwarf")
        assert hit is not None
        self.assertNotEqual(hit[1], "homebrew")

    def test_spellings_meet_as_the_rules_lookup_matches_them(self) -> None:
        for kept, said in (
            ("Half Elf", "half-elf"), ("Half-elf", "half elf"), ("Dragon-born", "Dragonborn"),
            ("Frost Elf (Homebrew)", "frost elf"),
        ):  # fmt: skip
            with self.subTest(kept=kept, said=said):
                own = traits_read.homebrew_lineages([E(kind="species", name=kept)])  # type: ignore[list-item]
                resolve = traits_read.rules_resolver("2024", "2014", own)
                self.assertEqual(resolve(said), (kept, "homebrew", None))

    def test_the_upload_s_traits_use_the_homebrew_given(self) -> None:
        from dmbot.memory import background as bg
        from dmbot.ui import name_background as nb

        found = [bg.Found("Mira Vell", "npc", (), (), "")]
        campaign = E(target_ruleset="2024", fallback_ruleset="2014")
        own = traits_read.homebrew_lineages([E(kind="species", name="Half-elf")])  # type: ignore[list-item]
        got = nb._traits_of(
            ['- Mira Vell | lineage | half-elf | quote: "Mira Vell is a half-elf"'],
            TEXT, found, (), campaign, Counter(), own,  # type: ignore[arg-type]
        )  # fmt: skip
        (trait,) = next(iter(got.values()))
        self.assertEqual((trait.name, trait.rules_source), ("Half-elf", "homebrew"))

    def test_only_lineages_and_the_first_of_a_name(self) -> None:
        entries: list[Any] = [
            E(kind="species", name="Frost Elf"),  # the campaign's own
            E(kind="spell", name="Frost Bolt"),  # not a lineage
            E(kind="species", name="frost elf"),  # a followed pack's, behind the campaign's own
        ]
        got = traits_read.homebrew_lineages(entries)
        self.assertEqual(set(got.values()), {"Frost Elf"})


class Reader:
    def __init__(self) -> None:
        self.asked: list[tuple[int, str]] = []

    async def list(self, gid: int, cid: str) -> list[object]:
        self.asked.append((gid, cid))
        return [E(kind="species", name="Frost Elf")]


class Broken:
    async def list(self, gid: int, cid: str) -> list[object]:
        raise RuntimeError("down")


CAMPAIGN = E(guild_id=7, id="c1")


class TheUploadsHomebrewRead(unittest.IsolatedAsyncioTestCase):
    async def test_it_reads_this_campaigns_homebrew(self) -> None:
        from dmbot.ui import name_background as nb

        reader = Reader()
        got = await nb._homebrew_lineages(E(homebrew_reader=reader), CAMPAIGN)  # type: ignore[arg-type]
        self.assertEqual((set(got.values()), reader.asked), ({"Frost Elf"}, [(7, "c1")]))

    async def test_no_reader_gives_none(self) -> None:
        from dmbot.ui import name_background as nb

        self.assertEqual(await nb._homebrew_lineages(E(), CAMPAIGN), {})  # type: ignore[arg-type]

    async def test_a_failed_read_gives_none_and_logs_the_type_only(self) -> None:
        from dmbot.ui import name_background as nb

        with self.assertLogs("dmbot.ui.name_background", level="WARNING") as logs:
            got = await nb._homebrew_lineages(E(homebrew_reader=Broken()), CAMPAIGN)  # type: ignore[arg-type]
        self.assertEqual(got, {})
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("down", logs.output[0])


class TheHeading(unittest.TestCase):
    def test_pass_1_s_traits_heading_is_its_own_section(self) -> None:
        listed = lore_read.parse_plain(
            "NAMES\n- Torvin Ashgrove | npc\nTRAITS\n- Torvin Ashgrove | lineage | dwarf | "
            'quote: "is a dwarf"\n'
        )
        self.assertEqual(len(listed.traits), 1)
        self.assertEqual(len(listed.names), 1)
        self.assertIn("TRAITS", lore_read.read_instructions())


class TheCardLine(unittest.TestCase):
    def link(
        self, kind: str, name: str, known: bool = True, edition: str | None = None
    ) -> RuleLink:
        return RuleLink("a" * 32, "b" * 32, kind, "SRD 5.2.1" if known else "", name, edition,
                        known, 1)  # fmt: skip

    def test_lineage_is_the_word_and_legacy_and_unknown_say_so(self) -> None:
        line = traits_line(
            [
                self.link(CLASS, "Cleric"),
                self.link(SPECIES, "Half-Elf", edition="2014"),
                self.link(CLASS, "Artificer", known=False),
            ]
        )
        self.assertEqual(
            line,
            "\n🧬 **Lineage:** Half-Elf [Legacy 2014] · **Class:** Cleric, Artificer "
            "(not in DMbot's rules)",
        )
        self.assertEqual(traits_line([]), "")


GUILD, DM = 1, 7


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    async def add(self, *names: NewName) -> tuple[list[str | None], int | None]:
        written = await self.memory.add_names(
            GUILD, self.campaign.id, list(names), source=DM_SOURCE
        )
        return written.value, written.batch

    async def test_a_new_character_s_traits_are_written_with_it_and_undone_with_it(self) -> None:
        traits = (
            Trait(SPECIES, "Dwarf", "SRD 5.2.1", "2024", True),
            Trait(SPECIES, "Elf", "SRD 5.2.1", "2024", True),  # (one lineage: the first)
            Trait(CLASS, "Cleric", "SRD 5.2.1", None, True),
            Trait(CLASS, "Cleric", "SRD 5.2.1", None, True),  # (no repeats)
        )
        (eid,), batch = await self.add(NewName("Torvin Ashgrove", "npc", PROPOSED, traits=traits))
        assert eid is not None and batch is not None
        links = await self.memory.rule_links(GUILD, self.campaign.id, [eid])
        self.assertEqual(sorted((r.kind, r.name) for r in links),
                         [(CLASS, "Cleric"), (SPECIES, "Dwarf")])  # fmt: skip
        await self.memory.undo_names(GUILD, self.campaign.id, batch)
        self.assertEqual(await self.memory.rule_links(GUILD, self.campaign.id, [eid]), [])

    async def test_a_place_never_gets_a_lineage(self) -> None:
        (eid,), _ = await self.add(
            NewName("Deepwood", "place", PROPOSED, traits=(Trait(SPECIES, "Elf"),))
        )
        assert eid is not None
        self.assertEqual(await self.memory.rule_links(GUILD, self.campaign.id, [eid]), [])

    async def test_a_known_trait_without_its_source_is_left_out(self) -> None:
        (eid,), _ = await self.add(
            NewName("Mira Vell", "npc", PROPOSED, traits=(Trait(CLASS, "Paladin", "", None, True),))
        )
        assert eid is not None
        self.assertEqual(await self.memory.rule_links(GUILD, self.campaign.id, [eid]), [])


if __name__ == "__main__":
    unittest.main()
