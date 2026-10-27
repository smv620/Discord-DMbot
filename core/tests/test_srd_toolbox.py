"""Reading the SRD 5.2.1's Gameplay Toolbox chapter into rules, poisons, traps and curses (#1935):
made-up lines in the shapes the real PDF has (no PDF is needed, and none is kept in the
repository)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.devtools.srd import build, toolbox
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, ITALIC, BODY = "GillSans-SemiBold", "Cambria-Italic", "Cambria"
BOLD, BOLD_LEAD, SANS = "Cambria-Bold", "Cambria-BoldItalic", "GillSans"
LEFT, RIGHT = 63.0, 314.0
DATA = Path(__file__).resolve().parents[1] / "src" / "dmbot" / "rules" / "data" / "srd52"
TERRAINS = (
    *("Arctic", "Coastal", "Desert", "Forest", "Grassland", "Hill"),
    *("Mountain", "Swamp", "Underdark", "Urban", "Waterborne"),
)


FIXTURE_LEFT_OUT = {
    "Creating a Background": 1,
    "1: Choose Abilities": 1,
    "2: Choose a Feat": 0,
    "3: Choose Skill Proficiencies": 0,
    "4: Choose a Tool Proficiency": 0,
    "5: Choose Equipment": 0,
    "Curses": 1,
    "Example Contagions": 1,
    "Environmental Effects": 0,
    "Sample Poisons": 1,
    "Example Traps": 1,
    "Potion Miscibility": 2,
    "Sentient Item’s Alignment": 2,
    "Sentient Item’s Communication": 2,
    "Sentient Item’s Senses": 2,
    "Sentient Item’s Special Purpose": 2,
}


def at(x: float, y: float, text: str, font: str = BODY, page: int = 192) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def terrain_table(top: float = 266.0) -> list[Line]:
    """The Travel Terrain table: its column heads, eleven rows (the last with a dagger on a line
    of its own above it) and its two notes."""
    lines = [at(67.0, top, toolbox.TERRAIN_HEADER, TITLE)]
    y = top - 15
    for name in TERRAINS:
        if name == "Waterborne":
            lines.append(at(162.0, y + 3, "† ", SANS))
        pace = "Fast*" if name == "Arctic" else "Special" if name == "Waterborne" else "Normal"
        lines.append(at(67.0, y, f"{name} {pace} 2d8 × 10 feet 10 15 20", SANS))
        y -= 15
    lines += [
        at(LEFT, y, "*Appropriate equipment is necessary to keep up a Fast pace.", SANS),
        at(LEFT, y - 6, "†", SANS),
        at(
            65.0, y - 9, "Characters’ rate of travel while waterborne depends on the vehicle.", SANS
        ),
    ]
    return lines


def two(x: float, y: float, label: str, x_rhs: float, rhs: str) -> Line:
    """A formula's bold label and, further along the same line, its right-hand side."""
    pieces = [
        Piece(x, y, "ABCDEF+Cambria-Bold", label),
        Piece(x_rhs, y, "ABCDEF+Cambria", rhs),
    ]
    return Line(x, y, pieces, 192)


def formulas() -> list[Line]:
    """The boxed pace formulas, as the PDF sets them: bold labels, an "=" on a line of its own."""
    return [
        Line(
            364.0,
            734.0,
            [
                Piece(364.0, 734.0, "ABCDEF+Cambria-Bold", "Miles per hour "),
                Piece(440.0, 734.0, "ABCDEF+Cambria", " Speed ÷ 10\n"),
            ],
            192,
        ),
        at(434.0, 733.0, "="),
        Line(
            327.0,
            721.0,
            [
                Piece(327.0, 721.0, "ABCDEF+Cambria-Bold", "Miles per day"),
                Piece(389.0, 721.0, "ABCDEF+Cambria", " (Normal pace) "),
                Piece(461.0, 721.0, "ABCDEF+Cambria", " Miles per hour \n"),
            ],
            192,
        ),
        at(456.0, 720.0, "="),
        at(340.0, 708.0, "× number of hours traveled (typically 8)"),
        two(328.0, 695.0, "Fast pace ", 379.0, " Miles per day × 1⅓ (round down)\n"),
        at(374.0, 694.0, "="),
        two(326.0, 682.0, "Slow pace ", 380.0, " Miles per day × 2/3 (round down)\n"),
        at(374.0, 681.0, "="),
    ]


def chapter() -> list[Line]:
    """The chapter in the shapes it has: a rule with a wrapped heading, the travel section with
    its formulas printed in the next column, a terrain table, a section that is left out, the
    sample fear table with a word cut at a line's end, bullets set in the serif font, a poison, a
    contagion, a trap with its levels table, a trap whose levels wrap, and an environmental curse
    that holds one named curse."""
    return [
        at(66.0, 723, "Gameplay Toolbox", TITLE),
        at(LEFT, 694, "Travel Pace", TITLE),
        at(LEFT, 680, "A group can travel at a Normal, Fast, or Slow pace."),
        at(LEFT, 650, "Special Movement", TITLE),
        at(LEFT, 633, "Translate the party’s Speed into travel rates using these rules:"),
        at(LEFT, 281, "Travel Terrain", TITLE),
        *terrain_table(),
        *formulas(),
        at(RIGHT, 666, "If the characters are flying, they can move at a Fast pace."),
        at(RIGHT, 622, "Vehicles", TITLE, 192),
        at(RIGHT, 605, "Characters in a vehicle use its speed."),
        at(RIGHT, 544, "Creating a Background", TITLE),
        at(RIGHT, 529, "Advice on building a background."),
        at(RIGHT, 401, "1: Choose Abilities", TITLE),
        at(RIGHT, 385, "Choose three abilities."),
        at(LEFT, 666, "2: Choose a Feat", TITLE, 193),
        at(LEFT, 618, "3: Choose Skill Proficiencies", TITLE, 193),
        at(LEFT, 545, "4: Choose a Tool Proficiency", TITLE, 193),
        at(LEFT, 496, "5: Choose Equipment", TITLE, 193),
        at(LEFT, 419, "Curses and ", TITLE, 193),
        at(LEFT, 400, "Magical Contagions", TITLE, 193),
        at(LEFT, 385, "A curse is a magical burden.", BODY, 193),
        at(LEFT, 305, "Curses", TITLE, 193),
        at(LEFT, 288, "A curse takes one of these forms.", BODY, 193),
        at(LEFT, 399, "Environmental Curses", TITLE, 193),
        at(LEFT, 386, "Some locations are suffused with evil.", BODY, 193),
        at(
            71.0,
            338,
            " Demonic Possession. Demonic Possession arises from the Abyss.",
            BOLD_LEAD,
            193,
        ),
        at(LEFT, 326, "A creature must succeed on a DC 15 Charisma saving throw.", BODY, 193),
        at(LEFT, 729, "Environmental Effects", TITLE, 195),
        at(LEFT, 729, "Fear and Mental Stress", TITLE, 196),
        at(LEFT, 714, "Adventurers are made of sterner stuff.", BODY, 196),
        at(LEFT, 598, "Fear Effects", TITLE, 196),
        at(LEFT, 581, "Use the Frightened condition as the baseline.", BODY, 196),
        at(LEFT, 499, "Sample Fear DCs", TITLE, 196),
        at(67.0, 484, "Example Save DC", TITLE, 196),
        at(67.0, 469, "When the characters open a sarcophagus, a 10", SANS, 196),
        at(67.0, 456, "harmless yet terrifying appari-", SANS, 196),
        at(67.0, 444, "tion appears.", SANS, 196),
        at(67.0, 429, "A portal to the Abyss opens, revealing a 20", SANS, 196),
        at(67.0, 416, "realm of torment.", SANS, 196),
        at(LEFT, 356, "At your discretion, consider these examples:", BODY, 196),
        at(LEFT, 279, "• The creature must take the Dash action on each of its", BODY, 196),
        at(74.0, 267, "turns.", BODY, 196),
        at(LEFT, 243, "• Attack rolls against the creature have Advantage.", BODY, 196),
        at(RIGHT, 732, "Sample Poisons", TITLE, 197),
        at(RIGHT, 715, "Example poisons are detailed here.", BODY, 197),
        at(RIGHT, 648, "Assassin’s Blood (150 GP)", TITLE, 197),
        at(RIGHT, 635, "Ingested Poison", ITALIC, 197),
        at(RIGHT, 619, "A creature must make a DC 10 Constitution saving throw.", BODY, 197),
        at(RIGHT, 552, "Burnt Othur Fumes (1,500 GP)", TITLE, 197),
        at(RIGHT, 539, "Inhaled Poison", ITALIC, 197),
        at(RIGHT, 524, "A creature must make a DC 13 Constitution saving throw.", BODY, 197),
        at(LEFT, 532, "Example Contagions", TITLE, 194),
        at(LEFT, 519, "The following examples show how contagions work.", BODY, 194),
        at(LEFT, 463, "Cackle Fever", TITLE, 194),
        at(LEFT, 451, "Magical Contagion", ITALIC, 194),
        at(LEFT, 435, "Cheaply made potions are tainted by Cackle Fever.", BODY, 194),
        at(
            71.0,
            220,
            "Fighting the Contagion. At the end of each Long Rest, a DC 13 save.",
            BOLD_LEAD,
            194,
        ),
        at(LEFT, 274, "Example Traps", TITLE, 199),
        at(LEFT, 258, "Traps are presented in alphabetical order.", BODY, 199),
        at(LEFT, 238, "Collapsing Roof", TITLE, 199),
        at(LEFT, 226, "Deadly Trap (Levels 1–4)", ITALIC, 199),
        at(LEFT, 211, "Trigger: A creature crosses a trip wire", BOLD, 199),
        at(LEFT, 199, "Duration: Instantaneous", BOLD, 199),
        at(LEFT, 181, "This trap uses a trip wire to collapse the ceiling.", BODY, 199),
        at(71.0, 133, " The first creature that crosses it takes damage.", BODY, 199),
        at(322.0, 638, " At Higher Levels. You can scale the trap.", BOLD_LEAD, 199),
        at(322.0, 598, "Levels Bludgeoning Damage Save DC", TITLE, 199),
        at(325.0, 583, "5–10 22 (4d10) 15", SANS, 199),
        at(323.0, 568, "11–16 55 (10d10) 17", SANS, 199),
        at(LEFT, 483, "Rolling Stone", TITLE, 201),
        at(LEFT, 470, "Deadly Trap (Levels 11–16) or Nuisance Trap (Levels", ITALIC, 201),
        at(LEFT, 458, "17–20)", ITALIC, 201),
        at(LEFT, 443, "Trigger: A creature moves onto a pressure plate", BOLD, 201),
        at(LEFT, 432, "Duration: Until the stone stops rolling", BOLD, 201),
        at(71.0, 353, " On its turn, the stone moves 60 feet.", BODY, 201),
    ]


XP_TABLE = (
    (1, "50 75 100"), (2, "100 150 200"), (3, "150 225 400"), (4, "250 375 500"),
    (5, "500 750 1,100"), (6, "600 1,000 1,400"), (7, "750 1,300 1,700"),
    (8, "1,000 1,700 2,100"), (9, "1,300 2,000 2,600"), (10, "1,600 2,300 3,100"),
    (11, "1,900 2,900 4,100"), (12, "2,200 3,700 4,700"), (13, "2,600 4,200 5,400"),
    (14, "2,900 4,900 6,200"), (15, "3,300 5,400 7,800"), (16, "3,800 6,100 9,800"),
    (17, "4,500 7,200 11,700"), (18, "5,000 8,700 14,200"), (19, "5,500 10,700 17,200"),
    (20, "6,400 13,200 22,000"),
)  # fmt: skip


def xp_table(page: int = 202, rows: tuple[tuple[int, str], ...] = XP_TABLE) -> list[Line]:
    """The XP Budget per Character table as the PDF has it: a caption, a boxed heading, the
    column head "Level" with "Low Moderate High" in the same line, and a row a level."""
    lines = [
        at(RIGHT, 730, "XP Budget per Character", TITLE, page),
        at(378.8, 715.5, "——— Encounter Difficulty ———", TITLE, page),
        at(317.5, 711.5, "Party’s ", TITLE, page),
        Line(
            317.5,
            700.8,
            [
                Piece(317.5, 700.8, f"ABCDEF+{TITLE}", "Level\n"),
                Piece(360.0, 700.8, f"ABCDEF+{TITLE}", "Low Moderate High\n"),
            ],
            page,
        ),
    ]
    for n, (level, cells) in enumerate(rows):
        lines.append(at(333.0, 686 - 15 * n, f"{level} {cells}", SANS, page))
    return lines


def encounters(rows: tuple[tuple[int, str], ...] = XP_TABLE) -> list[Line]:
    """The Combat Encounters section (#2000), in the order the PDF reads: the left column of the
    first page, then its right column (the table and Step 3), then the second page."""
    return [
        at(LEFT, 729, "Combat Encounters", TITLE, 202),
        at(LEFT, 714, "These features can make a combat encounter more interesting:", BODY, 202),
        at(LEFT, 684, "Changes in Elevation. Terrain that rewards clever positioning.", BOLD, 202),
        at(LEFT, 472, "Combat Encounter Difficulty", TITLE, 202),
        at(
            LEFT,
            456,
            "Use these guidelines to create an encounter of a desired difficulty.",
            BODY,
            202,
        ),
        at(LEFT, 424, "Step 1: Choose a Difficulty", TITLE, 202),
        at(LEFT, 412, "Three categories describe the range of encounter difficulty:", BODY, 202),
        at(
            LEFT,
            382,
            "Low Difficulty. An encounter of low difficulty has a scary moment.",
            BOLD,
            202,
        ),
        at(LEFT, 159, "Step 2: Determine Your XP Budget", TITLE, 202),
        at(LEFT, 146, "Cross-reference the party’s level with the desired difficulty.", BODY, 202),
        at(LEFT, 134, "Multiply the number in the table by the number of characters.", BODY, 202),
        *xp_table(rows=rows),
        at(RIGHT, 375, "Step 3: Spend Your Budget", TITLE, 202),
        at(RIGHT, 362, "Every creature has an XP value in its stat block.", BODY, 202),
        at(
            RIGHT,
            273,
            "Example 1. A low-difficulty encounter for four level 1 characters.",
            BOLD,
            202,
        ),
        at(RIGHT, 219, "• 1 Bugbear Warrior (200 XP)", BOLD, 202),
        at(RIGHT, 206, "• 2 Giant Wasps (100 XP each), for 200 XP total", BOLD, 202),
        at(LEFT, 647, "Troubleshooting", TITLE, 203),
        at(LEFT, 630, "Keep the following in mind.", BODY, 203),
        at(LEFT, 599, "Many Creatures", TITLE, 203),
        at(LEFT, 586, "The more creatures, the higher the risk of a lucky streak.", BODY, 203),
        at(LEFT, 495, "Adjustments", TITLE, 203),
        at(LEFT, 482, "A player’s absence might warrant removing creatures.", BODY, 203),
        at(RIGHT, 733, "CR 0 Creatures", TITLE, 203),
        at(RIGHT, 720, "Use CR 0 creatures sparingly.", BODY, 203),
        at(RIGHT, 665, "Number of Stat Blocks", TITLE, 203),
        at(RIGHT, 652, "Be mindful of the number of stat blocks.", BODY, 203),
        at(RIGHT, 561, "Powerful Creatures", TITLE, 203),
        at(
            RIGHT,
            548,
            "A creature whose CR is higher than the level may deal heavy damage.",
            BODY,
            203,
        ),
        at(RIGHT, 469, "Unusual Features", TITLE, 203),
        at(
            RIGHT,
            456,
            "Consider not adding a monster with a feature low levels cannot beat.",
            BODY,
            203,
        ),
    ]


SANS_BOLD_ITALIC, BOX_TITLE = "GillSans-BoldItalic", "GillSans-Bold-SC700"
CATEGORIES = (
    ("Armor", "+1 Leather Armor, +1 Shield"), ("Potions", "Potion of Healing"),
    ("Rings", "Ring of Invisibility"), ("Rods", "Immovable Rod"), ("Scrolls", "Spell Scroll"),
    ("Staffs", "Staff of Striking"), ("Wands", "Wand of Fireballs"),
    ("Weapons", "+1 Ammunition, +1 Longsword"),
    ("Wondrous Items", "Bag of Holding, Boots of Elvenkind"),
)  # fmt: skip
CRAFT_TOOLS = (
    "Armor Leatherworker’s Tools, Smith’s", "Tools, or Weaver’s Tools depend-",
    "ing on the kind of armor", "Potion Alchemist’s Supplies or Herbalism", "Kit",
    "Ring Jeweler’s Tools", "Rod Woodcarver’s Tools", "Scroll Calligrapher’s Supplies",
    "Staff Woodcarver’s Tools", "Wand Woodcarver’s Tools", "Weapon Smith’s Tools",
    "Wondrous Item Tinker’s Tools",
)  # fmt: skip
CRAFT_COSTS = (
    "Common 5 days 50 GP", "Uncommon 10 days 200 GP", "Rare 50 days 2,000 GP",
    "Very Rare 125 days 20,000 GP", "Legendary 250 days 100,000 GP",
)  # fmt: skip
MAGIC_TITLES_IN_ORDER = (
    "Magic Items", "Magic Item Categories", "Magic Armor", "Magic Potions", "Magic Rings",
    "Wondrous Items", "Magic Item Rules", "Attunement Prerequisites",
    "Items Made for Specific Creatures", "Unusual Anatomy", "Paired Items", "Magic Item Rarity",
    "Magic Item Values by Rarity", "Activating a Magic Item", "Command Word", "Consumable Items",
    "Spells Cast from Items", "Magic Item Charges", "The Next Dawn", "Cursed Items",
    "Magic Item Resilience", "Crafting Magic Items", "Crafting: Arcana Proficiency",
    "Crafting: Tools", "Crafting: Spells", "Crafting: Time and Cost", "Sentient Magic Items",
    "Sentient Magic Item Traits", "Sentient Item Abilities", "Sentient Item Alignment",
    "Sentient Item Communication", "Sentient Item Senses", "Sentient Item Special Purpose",
    "Sentient Item Conflict",
)  # fmt: skip
SENTIENT_TABLES = (
    "Sentient Item’s Alignment", "Sentient Item’s Communication", "Sentient Item’s Senses",
    "Sentient Item’s Special Purpose",
)  # fmt: skip


def lead_in(x: float, y: float, name: str, words: str, page: int) -> Line:
    """A box paragraph's first line: its bold-italic lead-in, then plain sans words."""
    pieces = [
        Piece(x, y, f"ABCDEF+{SANS_BOLD_ITALIC}", f"{name}."),
        Piece(x + 90, y, f"ABCDEF+{SANS}", f" {words}"),
    ]
    return Line(x, y, pieces, page)


def column(x: float, page: int, *specs: tuple[str, str]) -> list[Line]:
    """Lines going down a column, each as (font, text)."""
    return [at(x, 720 - 14 * n, text, font, page) for n, (font, text) in enumerate(specs)]


def magic_items(
    categories: tuple[tuple[str, str], ...] = CATEGORIES,
    costs: tuple[str, ...] = CRAFT_COSTS,
    tools: tuple[str, ...] = CRAFT_TOOLS,
) -> list[Line]:
    """The Magic Items section (#2001), in the order the PDF reads: a page heading set away from
    the column's edge, the categories and their table, the potions with the table of random
    results beside them, the box of Magic Item Rules in the sans font, the rarity values, the
    crafting tables and the sentient items with their random tables."""
    return [
        *column(105.0, 204, (TITLE, "Magic Items")),
        *column(LEFT, 204, (BODY, "Magic items are gleaned from the hoards of felled monsters.")),
        *column(LEFT, 204, (TITLE, "Magic Item Categories"), (BODY, "Every item has a category.")),
        *column(LEFT, 204, (TITLE, "Magic Item Categories")),
        at(67.0, 600, "Category Examples", TITLE, 204),
        *(at(66.0, 586 - 12 * n, f"{c} {e}", SANS, 204) for n, (c, e) in enumerate(categories)),
        *column(LEFT, 204, (TITLE, "Armor"), (BODY, "An item in the Armor category is armor.")),
        *column(LEFT, 204, (TITLE, "Potions"), (BODY, "A potion is a magical brew.")),
        *column(LEFT, 204, (BOLD_LEAD, "Mixing Potions. A character might mix two potions.")),
        *column(RIGHT, 204, (BODY, "Roll on the Potion Miscibility table.")),
        *column(RIGHT, 204, (TITLE, "Potion Miscibility")),
        at(319.0, 700, "1d100 Result", TITLE, 204),
        at(326.0, 688, "01 Both potions lose their effects.", SANS, 204),
        *column(RIGHT, 204, (TITLE, "Rings"), (BODY, "A ring must be worn.")),
        *column(LEFT, 205, (TITLE, "Wondrous Items"), (BODY, "Wondrous items include boots.")),
        at(322.0, 700, "Magic Item Rules", BOX_TITLE, 205),
        at(322.0, 686, "Rules for using magic items appear in “Equipment.”", SANS, 205),
        lead_in(329.0, 672, "Attunement Prerequisites", "If an item has a class prerequi-", 205),
        at(322.0, 660, "site, a creature must be a member of that class.", SANS, 205),
        lead_in(329.0, 646, "Items Made for Specific Creatures", "You can decide that an", 205),
        at(322.0, 634, "item doesn’t adjust its size to fit any wearer.", SANS, 205),
        lead_in(329.0, 620, "Unusual Anatomy", "Use your discretion.", 205),
        lead_in(329.0, 606, "Paired Items", "You can allow exceptions.", 205),
        *column(RIGHT, 205, (TITLE, "Magic Item Rarity"), (BODY, "Every magic item has a rarity.")),
        *column(RIGHT, 205, (TITLE, "Magic Item Values by Rarity")),
        *column(RIGHT, 205, (BODY, "Common items can often be bought in a town or a city.")),
        *column(LEFT, 206, (TITLE, "Magic Item Rarities and Values")),
        at(67.0, 700, "Rarity Value* Rarity Value*", TITLE, 206),
        at(66.0, 688, "Common 100 GP Very Rare 40,000 GP", SANS, 206),
        at(66.0, 676, "Uncommon 400 GP Legendary 200,000 GP", SANS, 206),
        at(66.0, 664, "Rare 4,000 GP Artifact Priceless", SANS, 206),
        at(
            63.0,
            652,
            "*Halve the value for a consumable item other than a Spell Scroll.",
            SANS,
            206,
        ),
        at(63.0, 640, "The value of a Spell Scroll is double what it costs to scribe.", SANS, 206),
        *column(LEFT, 206, (TITLE, "Activating a Magic Item"), (BODY, "It takes a Magic action.")),
        *column(LEFT, 206, (TITLE, "Command Word"), (BODY, "Say it, depending on the item.")),
        *column(LEFT, 206, (TITLE, "Consumable Items"), (BODY, "A potion is used up.")),
        *column(LEFT, 206, (TITLE, "Spells Cast from Items"), (BODY, "Use the item’s save DC.")),
        *column(RIGHT, 206, (TITLE, "Charges"), (BODY, "Some items have charges.")),
        *column(RIGHT, 206, (TITLE, "“The Next Dawn”"), (BODY, "Items recharge at dawn.")),
        *column(RIGHT, 206, (TITLE, "Cursed Items"), (BODY, "A description says if it is cursed.")),
        *column(RIGHT, 206, (TITLE, "Magic Item Resilience"), (BODY, "A magic item is durable.")),
        *column(RIGHT, 206, (TITLE, "Crafting Magic Items"), (BODY, "Follow the rules below.")),
        *column(RIGHT, 206, (TITLE, "Arcana Proficiency"), (BODY, "You need proficiency.")),
        *column(RIGHT, 206, (TITLE, "Tools"), (BODY, "The Magic Item Tools table lists them.")),
        *column(LEFT, 207, (TITLE, "Magic Item Tools")),
        at(67.0, 700, "Item Category Required Tool", TITLE, 207),
        *(at(66.0, 688 - 12 * n, row, SANS, 207) for n, row in enumerate(tools)),
        *column(LEFT, 207, (TITLE, "Spells"), (BODY, "Prepare every spell the item casts.")),
        *column(LEFT, 207, (TITLE, "Time and Cost"), (BODY, "Crafting takes time and money.")),
        *column(RIGHT, 207, (TITLE, "Magic Item Crafting Time and Cost")),
        at(318.0, 700, "Item Rarity Time* Cost*", TITLE, 207),
        *(at(317.0, 688 - 12 * n, row, SANS, 207) for n, row in enumerate(costs)),
        at(313.0, 620, "*The time and cost are halved for a consumable item", SANS, 207),
        at(313.0, 608, "other than a Spell Scroll.", SANS, 207),
        *column(RIGHT, 207, (TITLE, "Sentient Magic Items"), (BODY, "Some items have sentience.")),
        *column(RIGHT, 207, (TITLE, "Sentient Magic Item Traits"), (BODY, "Choose these.")),
        *column(RIGHT, 207, (TITLE, "Abilities"), (BODY, "An item has three scores.")),
        *column(RIGHT, 207, (TITLE, "Alignment"), (BODY, "An item has an alignment.")),
        *column(RIGHT, 207, (TITLE, "Communication"), (BODY, "An item can speak.")),
        *column(RIGHT, 207, (TITLE, "Senses"), (BODY, "An item can perceive.")),
        *column(LEFT, 208, (TITLE, "Special Purpose"), (BODY, "You can give an item a goal.")),
        *(
            line
            for name in SENTIENT_TABLES
            for line in column(
                LEFT, 208, (TITLE, name), (SANS, "1d10 Result"), (SANS, "1 A result.")
            )
        ),
        *column(RIGHT, 208, (TITLE, "Conflict"), (BODY, "The bearer makes a saving throw.")),
    ]


def parsed() -> toolbox.Toolbox:
    return toolbox.parse_toolbox([*chapter(), *encounters(), *magic_items()])


class Rules(unittest.TestCase):
    def test_every_kept_title_is_a_rule_and_the_left_out_ones_are_not(self) -> None:
        names = [r.name for r in parsed().rules]
        self.assertEqual(
            names,
            [
                "Travel Pace",
                "Special Movement",
                "Travel Terrain",
                "Vehicles",
                "Curses and Magical Contagions",
                "Environmental Curses",
                "Fear and Mental Stress",
                "Fear Effects",
                "Cackle Fever",
                *ENCOUNTER_TITLES,
            ],
        )

    def test_the_combat_encounters_section_is_twelve_rules_tagged_with_its_name(self) -> None:
        rules = {r.name: r for r in parsed().rules}
        for title in ENCOUNTER_TITLES:
            self.assertEqual(rules[title].tag, "Combat Encounters", title)
        self.assertEqual(rules["Combat Encounters"].page, 202)
        self.assertEqual(rules["Unusual Features"].page, 203)
        self.assertIn("Low Difficulty.", rules["Step 1: Choose a Difficulty"].text)
        self.assertIn("• 2 Giant Wasps (100 XP each)", rules["Step 3: Spend Your Budget"].text)

    def test_the_xp_budget_table_is_a_line_a_level_with_the_numbers_as_printed(self) -> None:
        text = {r.name: r for r in parsed().rules}["Step 2: Determine Your XP Budget"].text
        lines = [line for line in text.splitlines() if line.startswith("Level ")]
        self.assertEqual(len(lines), 20)
        self.assertIn("Level 1: Low 50, Moderate 75, High 100 XP per character", lines)
        self.assertIn("Level 3: Low 150, Moderate 225, High 400 XP per character", lines)
        self.assertIn("Level 10: Low 1,600, Moderate 2,300, High 3,100 XP per character", lines)
        self.assertIn("Level 20: Low 6,400, Moderate 13,200, High 22,000 XP per character", lines)
        self.assertTrue(text.startswith("Cross-reference the party"))
        self.assertNotIn("Moderate High", text.replace("Moderate 75, High", ""))  # no column head

    def test_a_heading_that_wraps_is_one_title(self) -> None:
        rule = next(r for r in parsed().rules if r.name.startswith("Curses and"))
        self.assertEqual(rule.text, "A curse is a magical burden.")

    def test_a_title_just_below_another_is_not_part_of_it(self) -> None:
        # "Fear and Mental Stress" / "Fear Effects" are lines apart; only a title that ends on
        # "and" (or a word like it) carries on.
        names = [r.name for r in parsed().rules]
        self.assertIn("Fear and Mental Stress", names)
        self.assertIn("Fear Effects", names)

    def test_the_formulas_follow_the_sentence_that_promises_them(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Special Movement")
        self.assertEqual(
            rule.text.splitlines(),
            [
                "Translate the party’s Speed into travel rates using these rules:",
                "Miles per hour = Speed ÷ 10",
                "Miles per day (Normal pace) = Miles per hour × number of hours traveled "
                "(typically 8)",
                "Fast pace = Miles per day × 1⅓ (round down)",
                "Slow pace = Miles per day × 2/3 (round down)",
                "If the characters are flying, they can move at a Fast pace.",
            ],
        )

    def test_the_terrain_table_is_a_line_a_terrain_and_two_notes(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Travel Terrain")
        rows = rule.text.splitlines()
        self.assertEqual(len(rows), 13)
        self.assertEqual(
            rows[0],
            "Arctic: Fast* Maximum Pace; Encounter Distance 2d8 × 10 feet; Foraging DC 10; "
            "Navigation DC 15; Search DC 20",
        )
        self.assertTrue(rows[10].startswith("Waterborne: Special† Maximum Pace"))
        self.assertTrue(rows[11].startswith("*Appropriate equipment"))
        self.assertTrue(rows[12].startswith("†Characters’ rate of travel"))

    def test_the_fear_table_is_put_in_the_text_a_line_a_row_with_cut_words_mended(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Fear Effects")
        self.assertEqual(
            rule.text.splitlines(),
            [
                "Use the Frightened condition as the baseline.",
                "DC 10: When the characters open a sarcophagus, a harmless yet terrifying "
                "apparition appears.",
                "DC 20: A portal to the Abyss opens, revealing a realm of torment.",
                "At your discretion, consider these examples:",
                "• The creature must take the Dash action on each of its turns.",
                "• Attack rolls against the creature have Advantage.",
            ],
        )

    def test_a_contagion_is_a_rule_with_its_kind_as_the_tag(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Cackle Fever")
        self.assertEqual(rule.tag, "Magical Contagion")
        self.assertIn("Fighting the Contagion. At the end of each Long Rest", rule.text)

    def test_the_left_out_titles_are_named_with_their_lines(self) -> None:
        self.assertEqual(
            toolbox.left_out([*chapter(), *magic_items()]),
            FIXTURE_LEFT_OUT,
        )


ENCOUNTER_TITLES = (
    "Combat Encounters",
    "Combat Encounter Difficulty",
    "Step 1: Choose a Difficulty",
    "Step 2: Determine Your XP Budget",
    "Step 3: Spend Your Budget",
    "Troubleshooting",
    "Many Creatures",
    "Adjustments",
    "CR 0 Creatures",
    "Number of Stat Blocks",
    "Powerful Creatures",
    "Unusual Features",
)


class MagicItems(unittest.TestCase):
    """The Magic Items section's rules (#2001)."""

    def rules(self, **fixtures: Any) -> dict[str, toolbox.MagicRule]:
        lines = [*chapter(), *encounters(), *magic_items(**fixtures)]
        return {r.name: r for r in toolbox.parse_toolbox(lines).magic}

    def test_every_title_is_a_rule_named_for_what_it_is_about(self) -> None:
        self.assertEqual(list(self.rules()), list(MAGIC_TITLES_IN_ORDER))
        for rule in self.rules().values():
            self.assertEqual(rule.tag, "Magic Items")

    def test_the_gms_advice_is_in_the_gameplay_toolbox_and_the_players_rules_are_not(self) -> None:
        gm = {n for n, r in self.rules().items() if r.section == "Gameplay Toolbox"}
        self.assertEqual(
            gm,
            {
                "Items Made for Specific Creatures",
                "Unusual Anatomy",
                "Paired Items",
                *(n for n in MAGIC_TITLES_IN_ORDER if n.startswith("Sentient")),
            },
        )
        for name in ("Attunement Prerequisites", "Magic Item Rarity", "Crafting: Tools"):
            self.assertEqual(self.rules()[name].section, "Magic Items", name)

    def test_the_page_heading_set_off_the_edge_starts_the_section(self) -> None:
        rules = self.rules()
        self.assertIn("hoards of felled monsters", rules["Magic Items"].text)
        self.assertNotIn(
            "hoards", next(r for r in parsed().rules if r.name == "Unusual Features").text
        )

    def test_the_categories_table_is_a_line_a_category_after_the_sentence(self) -> None:
        text = self.rules()["Magic Item Categories"].text
        self.assertTrue(text.startswith("Every item has a category.\nArmor: +1 Leather Armor"))
        self.assertIn("Wondrous Items: Bag of Holding, Boots of Elvenkind", text)
        self.assertEqual(text.count("\n"), 9)

    def test_the_rarity_values_are_a_line_a_rarity_in_order_with_the_note(self) -> None:
        text = self.rules()["Magic Item Values by Rarity"].text
        self.assertIn(
            "Common: 100 GP\nUncommon: 400 GP\nRare: 4,000 GP\nVery Rare: 40,000 GP\n"
            "Legendary: 200,000 GP\nArtifact: Priceless\n*Halve the value",
            text,
        )

    def test_the_crafting_tables_join_wrapped_rows_and_mend_a_cut_word(self) -> None:
        tools = self.rules()["Crafting: Tools"].text
        self.assertIn(
            "Armor: Leatherworker’s Tools, Smith’s Tools, or Weaver’s Tools depending on the "
            "kind of armor\nPotion: Alchemist’s Supplies or Herbalism Kit\n",
            tools,
        )
        cost = self.rules()["Crafting: Time and Cost"].text
        self.assertIn("Rare: 50 days, 2,000 GP\nVery Rare: 125 days, 20,000 GP", cost)
        self.assertTrue(cost.endswith("other than a Spell Scroll."))

    def test_the_box_is_split_at_its_lead_ins_and_a_cut_word_is_mended(self) -> None:
        rules = self.rules()
        self.assertEqual(
            rules["Magic Item Rules"].text, "Rules for using magic items appear in “Equipment.”"
        )
        self.assertEqual(
            rules["Attunement Prerequisites"].text,
            "If an item has a class prerequisite, a creature must be a member of that class.",
        )
        self.assertEqual(rules["Unusual Anatomy"].text, "Use your discretion.")
        self.assertEqual(
            rules["Items Made for Specific Creatures"].text,
            "You can decide that an item doesn’t adjust its size to fit any wearer.",
        )

    def test_the_tables_of_random_results_are_left_out_not_read(self) -> None:
        for rule in self.rules().values():
            self.assertNotIn("1d100", rule.text)
            self.assertNotIn("A result.", rule.text)

    def test_the_wrapped_titles_and_unquoted_names(self) -> None:
        self.assertIn("Items recharge at dawn.", self.rules()["The Next Dawn"].text)


class MagicItemStops(unittest.TestCase):
    def stop(self, **fixtures: Any) -> str:
        with self.assertRaises(SrdError) as caught:
            toolbox.parse_toolbox([*chapter(), *magic_items(**fixtures)])
        return str(caught.exception)

    def test_a_category_it_cannot_read(self) -> None:
        self.assertIn(
            "can't read the row", self.stop(categories=(*CATEGORIES[:8], ("Gizmos", "x")))
        )

    def test_a_category_missing(self) -> None:
        self.assertIn("8 rows, not the 9", self.stop(categories=CATEGORIES[:8]))

    def test_a_crafting_cost_row_missing(self) -> None:
        self.assertIn("4 rows, not the 5", self.stop(costs=CRAFT_COSTS[:4]))

    def test_a_crafting_cost_row_it_cannot_read(self) -> None:
        self.assertIn("can't read", self.stop(costs=(*CRAFT_COSTS[:4], "Legendary soon 100 GP")))

    def test_a_crafting_tool_missing(self) -> None:
        self.assertIn("rows, not the 9", self.stop(tools=CRAFT_TOOLS[:-1]))

    def test_a_title_in_the_section_it_has_no_place_for(self) -> None:
        lines = [*chapter(), *magic_items(), at(LEFT, 90, "Brand New Rule", TITLE, 208)]
        with self.assertRaisesRegex(SrdError, "Page 208.*Brand New Rule"):
            toolbox.parse_toolbox(lines)


class Hazards(unittest.TestCase):
    def test_a_poison_has_its_type_price_and_words(self) -> None:
        poisons = {p.name: p for p in parsed().poisons}
        blood = poisons["Assassin’s Blood"]
        self.assertEqual((blood.type, blood.price), ("Ingested", "150 GP"))
        self.assertEqual(blood.text, "A creature must make a DC 10 Constitution saving throw.")
        self.assertEqual(poisons["Burnt Othur Fumes"].price, "1,500 GP")

    def test_a_trap_has_its_levels_trigger_duration_and_text_with_the_levels_table(self) -> None:
        roof = {t.name: t for t in parsed().traps}["Collapsing Roof"]
        self.assertEqual(roof.severity, "Deadly Trap (Levels 1–4)")
        self.assertEqual(roof.trigger, "A creature crosses a trip wire")
        self.assertEqual(roof.duration, "Instantaneous")
        self.assertEqual(
            roof.text.splitlines(),
            [
                "This trap uses a trip wire to collapse the ceiling.",
                "The first creature that crosses it takes damage.",
                "At Higher Levels. You can scale the trap.",
                "Levels 5–10: Bludgeoning Damage 22 (4d10); Save DC 15",
                "Levels 11–16: Bludgeoning Damage 55 (10d10); Save DC 17",
            ],
        )

    def test_a_levels_line_that_wraps_is_one_severity(self) -> None:
        stone = {t.name: t for t in parsed().traps}["Rolling Stone"]
        self.assertEqual(
            stone.severity, "Deadly Trap (Levels 11–16) or Nuisance Trap (Levels 17–20)"
        )
        self.assertEqual(stone.duration, "Until the stone stops rolling")

    def test_the_one_named_curse_is_split_from_the_section_it_is_printed_in(self) -> None:
        parsed_ = parsed()
        [curse] = parsed_.curses
        self.assertEqual(curse.name, "Demonic Possession")
        self.assertTrue(curse.text.startswith("Demonic Possession arises from the Abyss."))
        self.assertIn("DC 15 Charisma saving throw", curse.text)
        section = next(r for r in parsed_.rules if r.name == "Environmental Curses")
        self.assertEqual(section.text, "Some locations are suffused with evil.")


class Stops(unittest.TestCase):
    def encounter_stop(self, rows: tuple[tuple[int, str], ...]) -> str:
        with self.assertRaises(SrdError) as caught:
            toolbox.parse_toolbox([*chapter(), *encounters(rows)])
        return str(caught.exception)

    def test_an_xp_budget_table_with_a_level_missing(self) -> None:
        self.assertIn("19 levels, not the 20", self.encounter_stop(XP_TABLE[:19]))

    def test_an_xp_budget_row_it_cannot_read(self) -> None:
        rows = (*XP_TABLE[:4], (5, "500 750"), *XP_TABLE[5:])
        self.assertIn("can't read the row", self.encounter_stop(rows))

    def test_an_xp_budget_row_out_of_order(self) -> None:
        rows = (XP_TABLE[1], XP_TABLE[0], *XP_TABLE[2:])
        self.assertIn("can't read the row", self.encounter_stop(rows))

    def without(self, *texts: str) -> list[Line]:
        return [ln for ln in chapter() if not any(ln.text.startswith(t) for t in texts)]

    def test_a_poison_without_its_type(self) -> None:
        with self.assertRaisesRegex(SrdError, "Assassin’s Blood.*page 197.*no type"):
            toolbox.parse_toolbox(self.without("Ingested Poison"))

    def test_a_poison_with_a_type_it_does_not_know(self) -> None:
        lines = chapter()
        for ln in lines:
            if ln.text == "Ingested Poison":
                ln.pieces[0] = Piece(ln.x, ln.y, ln.pieces[0].font, "Swallowed Poison")
        with self.assertRaisesRegex(SrdError, "no type line"):
            toolbox.parse_toolbox(lines)

    def test_a_trap_without_its_trigger(self) -> None:
        with self.assertRaisesRegex(SrdError, "Collapsing Roof.*no Trigger"):
            toolbox.parse_toolbox(self.without("Trigger: A creature crosses"))

    def test_a_trap_without_its_duration(self) -> None:
        with self.assertRaisesRegex(SrdError, "Collapsing Roof.*no Duration"):
            toolbox.parse_toolbox(self.without("Duration: Instantaneous"))

    def test_a_trap_without_an_effect(self) -> None:
        lines = [
            ln for ln in chapter() if not (ln.page == 201 and ln.text.startswith(" On its turn"))
        ]
        with self.assertRaisesRegex(SrdError, "Rolling Stone.*no effect"):
            toolbox.parse_toolbox(lines)

    def test_a_title_it_has_no_place_for(self) -> None:
        lines = [*chapter(), at(LEFT, 90, "Brand New Hazard", TITLE, 201), at(LEFT, 78, "Words.")]
        with self.assertRaisesRegex(SrdError, "Page 201.*Brand New Hazard"):
            toolbox.parse_toolbox(lines)

    def test_a_table_it_does_not_know(self) -> None:
        lines = chapter()
        at_ = next(
            i for i, ln in enumerate(lines) if ln.text == "Levels Bludgeoning Damage Save DC"
        )
        lines[at_] = at(322.0, 598, "Levels Acid Damage Save DC", TITLE, 199)
        with self.assertRaisesRegex(SrdError, "table on page 199 that the tool doesn't know"):
            toolbox.parse_toolbox(lines)

    def test_a_name_that_comes_twice_in_a_kind(self) -> None:
        lines = [*chapter(), *(ln for ln in chapter() if 635 <= ln.y <= 650 and ln.page == 197)]
        lines.insert(len(lines), at(RIGHT, 620, "A creature takes damage.", BODY, 197))
        with self.assertRaisesRegex(SrdError, "comes twice"):
            toolbox.parse_toolbox(lines)

    def test_a_chapter_that_does_not_start_with_its_title(self) -> None:
        with self.assertRaisesRegex(SrdError, "don't start with"):
            toolbox.parse_toolbox(chapter()[1:])

    def test_a_left_out_title_that_is_not_in_the_chapter(self) -> None:
        with self.assertRaisesRegex(SrdError, "not in the chapter"):
            toolbox.left_out(self.without("1: Choose Abilities"))

    def test_four_formulas_not_three(self) -> None:
        lines = [
            ln for ln in chapter() if not (ln.page == 192 and ln.x >= 320 and 680 <= ln.y <= 682.5)
        ]
        with self.assertRaisesRegex(SrdError, "3 formulas, not the four"):
            toolbox.parse_toolbox(lines)

    def test_a_formula_that_is_only_an_equals_sign(self) -> None:
        with self.assertRaisesRegex(SrdError, "a formula with nothing on either side"):
            toolbox.parse_toolbox(self.without("Slow pace"))


class TheRealChapterCounts(unittest.TestCase):
    def test_the_expected_left_out_counts_are_the_ones_attribution_names(self) -> None:
        attribution = (DATA / "ATTRIBUTION.md").read_text(encoding="utf-8")
        for title in toolbox.EXPECTED_LEFT_OUT:
            self.assertIn(title, attribution)
        self.assertEqual(set(toolbox.EXPECTED_LEFT_OUT), set(toolbox.LEFT_OUT))


class TheData(unittest.TestCase):
    """The file the tool made from the real PDF (the rebuild is checked by running the tool
    twice, and by this: the file is exactly what `dump` makes of itself)."""

    text: str
    data: dict[str, Any]
    by_name: dict[tuple[str, str], dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (DATA / "toolbox.json").read_text(encoding="utf-8")
        cls.data = json.loads(cls.text)
        cls.by_name = {(e["kind"], e["name"]): e for e in cls.data["entries"]}

    def test_the_file_is_what_the_tool_writes(self) -> None:
        self.assertEqual(build.dump(self.data), self.text)

    def test_how_many_of_each(self) -> None:
        kinds = [e["kind"] for e in self.data["entries"]]
        self.assertEqual(
            {k: kinds.count(k) for k in ("rule", "poison", "trap", "curse")},
            {"rule": 87, "poison": 14, "trap": 8, "curse": 1},
        )

    def test_two_poisons_with_their_type_and_price(self) -> None:
        purple = self.by_name["poison", "Purple Worm Poison"]
        self.assertEqual((purple["type"], purple["price"]), ("Injury", "2,000 GP"))
        self.assertIn("DC 21 Constitution saving throw", purple["text"])
        self.assertIn("35 (10d6) Poison damage", purple["text"])
        blood = self.by_name["poison", "Assassin’s Blood"]
        self.assertEqual((blood["type"], blood["price"]), ("Ingested", "150 GP"))

    def test_a_traps_dcs_and_its_levels(self) -> None:
        pit = self.by_name["trap", "Spiked Pit"]
        self.assertEqual(pit["severity"], "Deadly Trap (Levels 1–4)")
        self.assertIn("DC 15 Intelligence (Investigation) check", pit["text"])
        self.assertIn(
            "Levels 11–16: Pit Depth 60 feet; Damage 21 (6d6) Bludgeoning plus 36 (8d8) Piercing",
            pit["text"],
        )
        roof = self.by_name["trap", "Collapsing Roof"]
        self.assertIn("DC 13 Dexterity saving throw", roof["text"])
        self.assertIn("DC 11 Wisdom (Perception) check", roof["text"])
        self.assertIn("Levels 17–20: Bludgeoning Damage 99 (18d10); Save DC 19", roof["text"])

    def test_travel_pace_is_there_with_its_terrain_table(self) -> None:
        terrain = self.by_name["rule", "Travel Terrain"]["text"]
        self.assertIn("Forest: Normal Maximum Pace; Encounter Distance 2d8 × 10 feet", terrain)
        self.assertIn("Foraging DC 10; Navigation DC 15; Search DC 15", terrain)
        move = self.by_name["rule", "Special Movement"]["text"]
        self.assertIn("Fast pace = Miles per day × 1⅓ (round down)", move)

    def test_every_entry_says_where_it_is_and_the_file_is_the_dms_chapter(self) -> None:
        self.assertEqual(self.data["section"], "Gameplay Toolbox")
        for e in self.data["entries"]:
            self.assertGreaterEqual(e["page"], 192)
            self.assertLessEqual(e["page"], 208)  # the Magic Items section ends there
            self.assertTrue(e["text"].strip())

    def test_the_combat_encounters_section_is_in_the_file_with_its_budget_lines(self) -> None:
        rules = [e for e in self.data["entries"] if e["kind"] == "rule"]
        mine = [e for e in rules if e.get("tag") == "Combat Encounters"]
        self.assertEqual([e["name"] for e in mine], list(ENCOUNTER_TITLES))
        budget = self.by_name["rule", "Step 2: Determine Your XP Budget"]["text"]
        for line in (
            "Level 1: Low 50, Moderate 75, High 100 XP per character",
            "Level 3: Low 150, Moderate 225, High 400 XP per character",
            "Level 10: Low 1,600, Moderate 2,300, High 3,100 XP per character",
            "Level 20: Low 6,400, Moderate 13,200, High 22,000 XP per character",
        ):
            self.assertIn(line, budget)
        self.assertEqual(budget.count("\nLevel "), 20)

    def test_the_magic_items_rules_are_in_the_file_with_their_values_and_sections(self) -> None:
        mine = [e for e in self.data["entries"] if e.get("tag") == "Magic Items"]
        self.assertEqual(len(mine), 39)
        sections = {e["name"]: e["section"] for e in mine}
        self.assertEqual(sections["Attunement Prerequisites"], "Magic Items")
        self.assertEqual(sections["Sentient Item Conflict"], "Gameplay Toolbox")
        self.assertEqual(sections["Paired Items"], "Gameplay Toolbox")
        values = self.by_name["rule", "Magic Item Values by Rarity"]["text"]
        for line in (
            "Common: 100 GP",
            "Uncommon: 400 GP",
            "Rare: 4,000 GP",
            "Very Rare: 40,000 GP",
            "Legendary: 200,000 GP",
            "Artifact: Priceless",
        ):
            self.assertIn(line, values)
        cost = self.by_name["rule", "Crafting: Time and Cost"]["text"]
        self.assertIn("Rare: 50 days, 2,000 GP", cost)
        self.assertIn("Legendary: 250 days, 100,000 GP", cost)
        categories = self.by_name["rule", "Magic Item Categories"]["text"]
        self.assertIn("Potions: Potion of Healing", categories)
        self.assertEqual(categories.count("\n"), 9)

    def test_nothing_the_glossary_already_has_by_name_is_repeated(self) -> None:
        glossary = json.loads((DATA / "rules.json").read_text(encoding="utf-8"))["entries"]
        taken = {e["name"].casefold() for e in glossary}
        mine = {e["name"].casefold() for e in self.data["entries"] if e["kind"] == "rule"}
        self.assertEqual(taken & mine, set())


if __name__ == "__main__":
    unittest.main()
