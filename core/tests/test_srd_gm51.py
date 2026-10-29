"""Reading the SRD 5.1's game master chapters into traps, diseases, poisons and rules (#1944):
made-up lines in the shapes the real PDF has (no PDF is needed, and none is kept in the
repository)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dmbot.devtools.srd import build, gm51
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.parse51 import Vocabulary
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, LEAD, ITALIC = "GillSans-SemiBold", "Cambria", "Cambria-BoldItalic", "Cambria-Italic"
SANS, SANS_BOLD = "Calibri", "Calibri-Bold"
LEFT, RIGHT = 57.6, 328.6
DATA = Path(__file__).resolve().parents[1] / "src" / "dmbot" / "rules" / "data" / "srd51"


def at(x: float, y: float, text: str, font: str = BODY, page: int = 196) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def chapters() -> list[Line]:
    """The chapters' shapes: a rule with its tables, a sample trap with its kind line, the Pits
    with four lead-ins, a disease, the three madness tables (one wraps), the object tables, the
    poisons table and the poisons under their lead-ins."""
    return [
        at(LEFT, 697, "Traps", TITLE, 195),
        at(LEFT, 683, "Traps can be found almost anywhere.", BODY, 195),
        at(LEFT, 670, "Trap Effects", TITLE, 196),
        at(LEFT, 658, "The effects of traps can range from inconvenient to deadly.", BODY, 196),
        at(LEFT, 454, "Trap Save DCs and Attack Bonuses", SANS_BOLD, 196),
        at(LEFT, 439, "Trap Danger Save DC Attack Bonus", SANS_BOLD, 196),
        at(LEFT, 428, "Setback 10–11 +3 to +5", SANS, 196),
        at(LEFT, 417, "Deadly 16–20 +9 to +12", SANS, 196),
        at(LEFT, 375, "Damage Severity by Level", SANS_BOLD, 196),
        at(LEFT, 360, "Character Level Setback Dangerous Deadly", SANS_BOLD, 196),
        at(LEFT, 349, "1st–4th 1d10 2d10 4d10", SANS, 196),
        at(RIGHT, 707, "Sample Traps", TITLE, 196),
        at(RIGHT, 688, "The traps here are in alphabetical order.", BODY, 196),
        at(RIGHT, 642, "Collapsing Roof", TITLE, 196),
        at(RIGHT, 630, "Mechanical trap", ITALIC, 196),
        at(RIGHT, 612, "A successful DC 15 Dexterity check disables the trip wire.", BODY, 196),
        at(LEFT, 366, "Pits", TITLE, 197),
        at(LEFT, 353, "Mechanical trap", ITALIC, 197),
        at(LEFT, 336, "Four basic pit traps are presented here.", BODY, 197),
        at(LEFT + 9, 324, "Simple Pit. A simple pit trap is a hole.", LEAD, 197),
        at(LEFT + 9, 312, "The DC to spot the pit is 10.", BODY, 197),
        at(LEFT + 9, 300, "Hidden Pit. This pit has a cover.", LEAD, 197),
        at(LEFT + 9, 288, "Locking Pit. The cover is spring-loaded.", LEAD, 197),
        at(LEFT + 9, 276, "Spiked Pit. This pit has spikes.", LEAD, 197),
        at(LEFT, 697, "Diseases", TITLE, 198),
        at(LEFT, 683, "A plague ravages the kingdom.", BODY, 198),
        at(LEFT, 380, "Sample Diseases", TITLE, 198),
        at(LEFT, 362, "The diseases here illustrate the variety.", BODY, 198),
        at(LEFT, 293, "Cackle Fever", TITLE, 198),
        at(LEFT, 280, "This disease targets humanoids.", BODY, 198),
        at(LEFT, 697, "Madness", TITLE, 201),
        at(LEFT, 683, "Madness can be a horror theme.", BODY, 201),
        at(LEFT, 423, "Madness Effects", TITLE, 201),
        at(LEFT, 404, "Madness can be short-term, long-term, or indefinite.", BODY, 201),
        at(LEFT, 232, "Short-Term Madness", SANS_BOLD, 201),
        at(64.0, 217, "d100 Effect (lasts 1d10 minutes)", SANS_BOLD, 201),
        at(62.0, 206, "01–20 The character retreats into his or her mind", SANS, 201),
        at(99.0, 195, "and becomes paralyzed.", SANS, 201),
        at(62.0, 184, "21–30 The character becomes incapacitated.", SANS, 201),
        at(RIGHT, 559, "Long-Term Madness", SANS_BOLD, 201),
        at(335.0, 544, "d100 Effect (lasts 1d10 × 10 hours)", SANS_BOLD, 201),
        at(332.0, 534, "01–10 The character feels compelled to repeat an activity.", SANS, 201),
        at(LEFT, 709, "Indefinite Madness", SANS_BOLD, 202),
        at(64.0, 694, "d100 Flaw (lasts until cured)", SANS_BOLD, 202),
        at(62.0, 683, "01–15 “Being drunk keeps me sane.”", SANS, 202),
        at(LEFT, 397, "Curing Madness", TITLE, 202),
        at(LEFT, 378, "A calm emotions spell can suppress madness.", BODY, 202),
        at(LEFT, 697, "Objects", TITLE, 203),
        at(LEFT, 683, "Characters can destroy any destructible object.", BODY, 203),
        at(LEFT, 532, "Statistics for Objects", TITLE, 203),
        at(LEFT, 519, "You can assign an Armor Class to a destructible object.", BODY, 203),
        at(LEFT, 394, "Object Armor Class", SANS_BOLD, 203),
        at(LEFT, 379, "Substance AC", SANS_BOLD, 203),
        at(LEFT, 368, "Cloth, paper, rope 11", SANS, 203),
        at(LEFT, 357, "Stone 17", SANS, 203),
        at(LEFT + 9, 279, "Hit Points. An object’s hit points measure damage.", LEAD, 203),
        at(LEFT, 165, "Object Hit Points", SANS_BOLD, 203),
        at(LEFT, 150, "Size Fragile Resilient", SANS_BOLD, 203),
        at(LEFT, 139, "Tiny (bottle, lock) 2 (1d4) 5 (2d4)", SANS, 203),
        at(LEFT, 697, "Poisons", TITLE, 204),
        at(LEFT, 683, "Poisons are illegal in most societies.", BODY, 204),
        at(LEFT, 335, "Poisons", SANS_BOLD, 204),
        at(LEFT, 320, "Item Type Price per Dose", SANS_BOLD, 204),
        at(LEFT, 310, "Assassin’s blood Ingested 150 gp", SANS, 204),
        at(LEFT, 299, "Drow poison Injury 200 gp", SANS, 204),
        at(LEFT, 133, "Sample Poisons", TITLE, 204),
        at(LEFT, 115, "Each type of poison has its own debilitating effects.", BODY, 204),
        at(
            RIGHT, 711, "Assassin’s Blood (Ingested). A creature must make a DC 10 save.", LEAD, 204
        ),
        at(RIGHT, 640, "Drow Poison (Injury). A creature must make a DC 13 save.", LEAD, 204),
        at(RIGHT, 628, "The creature wakes up if it takes damage.", BODY, 204),
    ]


def vocab(lines: list[Line]) -> Vocabulary:
    return Vocabulary({"the", "and", "you", "pit", "trap"}, lines, set())


def read(lines: list[Line] | None = None) -> gm51.GameMaster:
    lines = chapters() if lines is None else lines
    return gm51.parse_gm(lines, vocab(lines))


class Reading(unittest.TestCase):
    def test_what_is_kept_by_kind(self) -> None:
        got = read()
        self.assertEqual(
            [r.name for r in got.rules],
            [
                "Traps", "Trap Effects", "Diseases", "Madness", "Madness Effects",
                "Short-Term Madness", "Long-Term Madness", "Indefinite Madness",
                "Curing Madness", "Objects", "Statistics for Objects", "Poisons",
            ],
        )  # fmt: skip
        self.assertEqual(
            [t.name for t in got.traps],
            ["Collapsing Roof", "Simple Pit", "Hidden Pit", "Locking Pit", "Spiked Pit"],
        )
        self.assertEqual([d.name for d in got.diseases], ["Cackle Fever"])
        self.assertEqual([p.name for p in got.poisons], ["Assassin’s Blood", "Drow Poison"])

    def test_a_trap_has_its_kind_and_the_pits_are_four_traps(self) -> None:
        traps = {t.name: t for t in read().traps}
        self.assertEqual(traps["Collapsing Roof"].type, "Mechanical trap")
        self.assertIn("DC 15 Dexterity check", traps["Collapsing Roof"].text)
        self.assertEqual(
            traps["Simple Pit"].text, "A simple pit trap is a hole.\nThe DC to spot the pit is 10."
        )
        self.assertEqual(traps["Spiked Pit"].text, "This pit has spikes.")
        self.assertNotIn("Four basic", "".join(t.text for t in traps.values()))

    def test_a_poisons_price_and_type_come_from_the_table_and_its_words_from_the_lead_in(
        self,
    ) -> None:
        poisons = {p.name: p for p in read().poisons}
        drow = poisons["Drow Poison"]
        self.assertEqual((drow.type, drow.price), ("Injury", "200 GP"))
        self.assertEqual(
            drow.text,
            "A creature must make a DC 13 save. The creature wakes up if it takes damage.",
        )
        self.assertEqual(poisons["Assassin’s Blood"].price, "150 GP")

    def test_the_madness_tables_are_rules_of_their_own_and_a_wrapped_row_is_one_row(self) -> None:
        rules = {r.name: r.text for r in read().rules}
        self.assertEqual(
            rules["Short-Term Madness"].splitlines(),
            [
                "d100 Effect (lasts 1d10 minutes)",
                "01–20: The character retreats into his or her mind and becomes paralyzed.",
                "21–30: The character becomes incapacitated.",
            ],
        )
        self.assertIn("01–15: “Being drunk keeps me sane.”", rules["Indefinite Madness"])
        self.assertNotIn("01–20", rules["Madness Effects"])

    def test_the_trap_and_object_tables_are_lines_in_their_rule(self) -> None:
        rules = {r.name: r.text.splitlines() for r in read().rules}
        self.assertIn("Setback: save DC 10–11, attack bonus +3 to +5", rules["Trap Effects"])
        self.assertIn(
            "Level 1st–4th: setback 1d10, dangerous 2d10, deadly 4d10", rules["Trap Effects"]
        )
        objects = rules["Statistics for Objects"]
        self.assertIn("Stone: AC 17", objects)
        self.assertIn("Tiny (bottle, lock): fragile 2 (1d4), resilient 5 (2d4)", objects)
        self.assertLess(objects.index("Object Armor Class"), objects.index("Stone: AC 17"))

    def test_the_left_out_are_named(self) -> None:
        self.assertEqual(
            gm51.left_out(chapters()), {"Sample Traps": 1, "Sample Diseases": 1, "Pits intro": 1}
        )


class Stops(unittest.TestCase):
    def without(self, *texts: str) -> list[Line]:
        return [ln for ln in chapters() if not any(ln.text.startswith(t) for t in texts)]

    def test_a_trap_without_its_kind_line(self) -> None:
        lines = [ln for ln in chapters() if not (ln.text == "Mechanical trap" and ln.page == 196)]
        with self.assertRaisesRegex(SrdError, "Collapsing Roof.*page 196.*no kind line"):
            read(lines)

    def test_a_poison_whose_table_says_another_type(self) -> None:
        lines = chapters()
        for ln in lines:
            if ln.text.startswith("Drow poison Injury"):
                ln.pieces[0] = Piece(ln.x, ln.y, ln.pieces[0].font, "Drow poison Contact 200 gp")
        with self.assertRaisesRegex(SrdError, "Drow Poison.*table says Contact"):
            read(lines)

    def test_a_poison_with_no_price(self) -> None:
        with self.assertRaisesRegex(SrdError, "Drow Poison.*no price"):
            read(self.without("Drow poison"))

    def test_a_title_it_has_no_place_for(self) -> None:
        lines = [*chapters(), at(LEFT, 90, "Brand New Hazard", TITLE, 204), at(LEFT, 78, "Words.")]
        with self.assertRaisesRegex(SrdError, "Page 204.*Brand New Hazard"):
            read(lines)

    def test_a_table_it_does_not_know(self) -> None:
        lines = [*chapters(), at(LEFT, 90, "Mystery Table", SANS_BOLD, 204)]
        with self.assertRaisesRegex(SrdError, "table the tool doesn't know"):
            read(lines)

    def test_a_madness_table_that_is_missing(self) -> None:
        lines = [ln for ln in chapters() if ln.text != "Long-Term Madness"]
        with self.assertRaisesRegex(SrdError, "table the tool doesn't know"):
            read(lines)

    def test_pits_that_are_not_the_four(self) -> None:
        with self.assertRaisesRegex(SrdError, "Pits.*not"):
            read(self.without("Locking Pit"))

    def test_a_rule_with_no_words(self) -> None:
        lines = [*chapters(), at(LEFT, 90, "Complex Traps", TITLE, 204)]
        with self.assertRaisesRegex(SrdError, "Complex Traps.*no description"):
            read(lines)

    def test_a_name_that_comes_twice(self) -> None:
        lines = [*chapters(), at(LEFT, 90, "Cackle Fever", TITLE, 204), at(LEFT, 78, "Again.")]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            read(lines)


class Building(unittest.TestCase):
    def test_the_file_is_in_the_5_2_1_toolboxs_shape(self) -> None:
        lines = chapters()
        pages = [*([[]] * 10), lines, [at(LEFT, 700, "Magic Items", TITLE, 206)]]

        def header(kind: str, section: str | None) -> dict[str, str]:
            return {"kind": kind, "section": section or ""}

        with patch.dict(gm51.EXPECTED_LEFT_OUT, gm51.left_out(lines), clear=True):
            files = build.gm_files_51(pages, header, vocab(lines))
        data = files["toolbox.json"]
        self.assertEqual((data["kind"], data["section"]), ("toolbox", "Gameplay Toolbox"))
        kinds = [e["kind"] for e in data["entries"]]
        self.assertEqual({k: kinds.count(k) for k in ("rule", "trap", "disease", "poison")},
                         {"rule": 12, "trap": 5, "disease": 1, "poison": 2})  # fmt: skip
        poison = next(e for e in data["entries"] if e["kind"] == "poison")
        self.assertEqual(set(poison), {"kind", "name", "type", "price", "text", "page"})

    def test_the_build_stops_when_what_is_left_out_changes(self) -> None:
        lines = chapters()
        pages = [*([[]] * 10), lines, [at(LEFT, 700, "Magic Items", TITLE, 206)]]
        with self.assertRaisesRegex(SrdError, "leave out something new"):
            build.gm_files_51(pages, lambda k, s: {}, vocab(lines))  # (the real counts)


class TheData(unittest.TestCase):
    text: str
    data: dict[str, Any]
    by_name: dict[tuple[str, str], dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (DATA / "toolbox.json").read_text(encoding="utf-8")
        cls.data = json.loads(cls.text)
        cls.by_name = {(e["kind"], e["name"]): e for e in cls.data["entries"]}

    def test_the_file_is_what_the_tool_writes_and_is_the_dms_chapter(self) -> None:
        self.assertEqual(build.dump(self.data), self.text)
        self.assertEqual(
            (self.data["source"], self.data["edition"], self.data["section"]),
            ("SRD 5.1", "2014", "Gameplay Toolbox"),
        )

    def test_how_many_of_each(self) -> None:
        kinds = [e["kind"] for e in self.data["entries"]]
        self.assertEqual(
            {k: kinds.count(k) for k in ("rule", "trap", "disease", "poison")},
            {"rule": 17, "trap": 11, "disease": 3, "poison": 14},
        )

    def test_sight_rots_cure_is_eyebright_or_a_spell(self) -> None:
        text = self.by_name["disease", "Sight Rot"]["text"]
        self.assertIn("rare flower called Eyebright", text)
        self.assertIn("lesser restoration or heal", text)

    def test_a_sample_traps_dcs(self) -> None:
        roof = self.by_name["trap", "Collapsing Roof"]
        self.assertEqual(roof["type"], "Mechanical trap")
        self.assertIn("The DC to spot the trip wire is 10", roof["text"])
        self.assertIn("DC 15 Dexterity check using thieves’ tools", roof["text"])
        self.assertIn("22 (4d10) bludgeoning damage", roof["text"])

    def test_purple_worm_poisons_5_1_type_price_and_save(self) -> None:
        worm = self.by_name["poison", "Purple Worm Poison"]
        self.assertEqual((worm["type"], worm["price"]), ("Injury", "2,000 GP"))
        self.assertIn("DC 19 Constitution saving throw", worm["text"])
        self.assertIn("42 (12d6) poison damage", worm["text"])
        self.assertEqual(self.by_name["poison", "Drow Poison"]["type"], "Injury")

    def test_the_four_pits_and_no_pits_entry(self) -> None:
        for name in ("Simple Pit", "Hidden Pit", "Locking Pit", "Spiked Pit"):
            self.assertIn(("trap", name), self.by_name)
        self.assertNotIn(("trap", "Pits"), self.by_name)

    def test_nothing_is_empty_or_left_with_pdf_noise(self) -> None:
        for e in self.data["entries"]:
            self.assertTrue(e["text"].strip(), e["name"])
            self.assertNotIn("\t", e["text"])
            self.assertNotRegex(e["text"], r" - ", e["name"])


if __name__ == "__main__":
    unittest.main()
