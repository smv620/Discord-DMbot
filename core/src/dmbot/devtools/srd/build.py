"""Builds the rules data files from the SRD 5.2.1 PDF (#866) or the 2014 SRD 5.1 PDF (#873).

    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.2.1.pdf
    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.1.pdf

Which one it is, the tool reads from the PDF. It writes `spells.json`, `conditions.json`
and `monsters.json` into `src/dmbot/rules/data/srd52/` or `srd51/` (or `--out`). The same PDF
always gives the same files, so a change shows up in review. Build the 5.2.1 files first: the
5.1 text layer is rougher, and its cut words are put back using the words of the 5.2.1 data.

Where the PDFs come from: both are Wizards of the Coast's own releases under CC-BY-4.0, at
the addresses in `SOURCE_URL` and `SOURCE_URL_51`. Download one into a folder of its own
(it is not kept in the repository) and give that path. The files record the PDF's SHA-256,
so anyone can check that they were made from the file Wizards published. The attribution
the licence asks for is in `rules/data/srd52/ATTRIBUTION.md` and `srd51/ATTRIBUTION.md`.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dmbot.devtools.srd import (
    classes,
    classes51,
    equipment,
    equipment51,
    gear,
    gm51,
    items,
    items51,
    monsters,
    monsters51,
    origins,
    origins51,
    parse,
    parse51,
    rules51,
    toolbox,
)
from dmbot.devtools.srd.pdf import Line, read_pages

SOURCE = "SRD 5.2.1"
EDITION = "2024"
LICENCE = "CC-BY-4.0"
SOURCE_URL = "https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf"
DATA = Path(__file__).resolve().parents[2] / "rules" / "data"
OUT = DATA / "srd52"
SOURCE_51 = "SRD 5.1"
EDITION_51 = "2014"
SOURCE_URL_51 = "https://media.dndbeyond.com/compendium-images/srd/5.1/SRD_CC_v5.1.pdf"
OUT_51 = DATA / "srd51"
SPELLS_HEADING_51 = "Spell Descriptions"
AFTER_SPELLS_HEADING_51 = "Traps"
CONDITIONS_HEADING_51 = "Appendix PH-A:"
AFTER_CONDITIONS_HEADING_51 = "Appendix PH-B:"
CONDITIONS_51 = (
    "Blinded", "Charmed", "Deafened", "Exhaustion", "Frightened", "Grappled", "Incapacitated",
    "Invisible", "Paralyzed", "Petrified", "Poisoned", "Prone", "Restrained", "Stunned",
    "Unconscious",
)  # fmt: skip

MONSTERS_HEADING = "Monsters A–Z"
RACES_HEADING_51 = "Races"  # the chapter starts on page 3, with the contents before it
AFTER_RACES_HEADING_51 = "Barbarian"
BACKGROUNDS_HEADING_51 = "Backgrounds"
BACKGROUND_51 = "Acolyte"  # the one background the SRD 5.1 has
AFTER_BACKGROUND_HEADING_51 = "Suggested Characteristics"
FEAT_51 = "Grappler"  # the one feat the SRD 5.1 has
BACKGROUND_PAGES_51 = 60  # the Backgrounds section, before the Equipment chapter
EQUIPMENT_HEADING_51 = "Equipment"  # the chapter's first page; it ends on the page with Trade Goods
TRADE_GOODS_HEADING_51 = "Trade Goods"
MOUNTS_TITLE_51 = "Mounts and Vehicles"
HALF = equipment51.HALF_PAGE  # x: the right-hand column of a page starts beyond this
# y: under this, the gear table's page has the Container Capacity table (left out)
CONTAINERS_BELOW = 185.0
ITEMS_HEADING_51 = "Magic Items"
AFTER_ITEMS_HEADING_51 = (
    "Monsters"  # only bounds the pages read; the list itself ends at the next line
)
SENTIENT_HEADING_51 = "Sentient Magic Items"  # ends the list of items, on a page it shares
CLASSES_HEADING_51 = "Barbarian"  # the first class, which starts a page
AFTER_CLASSES_HEADING_51 = "Beyond 1st Level"  # starts in the middle of the Wizard's last page
CLASSES_SECTION = "Classes"
MONSTERS_HEADING_51 = "Monsters (A)"
MONSTER_APPENDICES_51 = (
    ("Appendix MM-A:", "Appendix MM-A: Miscellaneous Creatures"),
    ("Appendix MM-B:", "Appendix MM-B: Nonplayer Characters"),
)
# What a monster entry holds besides its name, section, page and text, in this order.
MONSTER_FIELDS = (
    "size", "type", "alignment", "ac", "hp", "hit_dice", "speed",
    *monsters.ABILITY_NAMES,
    "saves", "skills", "resistances", "vulnerabilities", "immunities", "senses", "languages",
    "cr", "xp", "challenge",
)  # fmt: skip
MONSTER_FIELDS_BY_EDITION = {
    EDITION: ("initiative", "gear", "pb"),
    EDITION_51: ("ac_note", "condition_immunities"),
}

CONTENTS_PAGES = 10  # the table of contents names every heading too: look after it
SPELLS_HEADING = "Spell Descriptions"
GLOSSARY_HEADING = "Rules Glossary"
AFTER_GLOSSARY_HEADING = "Gameplay Toolbox"
AFTER_TOOLBOX_HEADING = "Magic Items A–Z"  # the Magic Items rules end where the items begin
TOOLBOX_SECTION = "Gameplay Toolbox"
CLASSES_HEADING = "Classes"
AFTER_CLASSES_HEADING = "Character Origins"
FEATS_HEADING = "Feats"
AFTER_FEATS_HEADING = "Equipment"
WEAPONS_HEADING = "Weapons"
ARMOR_HEADING = "Armor"
AFTER_ARMOR_HEADING = "Tools"
GEAR_HEADING_52 = "Adventuring Gear"
MOUNTS_HEADING = "Mounts and Vehicles"
LIFESTYLE_HEADING = "Lifestyle Expenses"  # starts in the middle of the vehicles table's page
EQUIPMENT_SECTION = "Equipment"
ORIGINS_SECTION = "Character Origins"
ITEMS_HEADING = "Magic Items A–Z"
AFTER_ITEMS_HEADING = "Monsters"
ITEMS_SECTION = "Magic Items"


def first_page_of(pages: Sequence[Sequence[Line]], heading: str, after: int = 0) -> int:
    """The number of the first page, after page `after`, that starts with this heading."""
    for number, lines in enumerate(pages, 1):
        if (
            number > after
            and lines
            and parse51.clean(lines[0].text) == heading
            and parse.is_title(lines[0])
        ):
            return number
    raise parse.SrdError(f"Can't find the heading {heading!r}: is this the SRD it should be?")


def lines_between(pages: Sequence[Sequence[Line]], first: int, last: int) -> list[Line]:
    """The lines of pages `first` up to (not including) `last`, in reading order."""
    return [line for page in pages[first - 1 : last - 1] for line in page]


def sha256_of(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def which_document(pages: Sequence[Sequence[Line]]) -> str:
    """ "5.2.1" or "5.1", from the PDF's first pages (the running footer names it)."""
    text = parse51.clean(" ".join(line.text for page in pages[:3] for line in page))
    if "System Reference Document 5.2.1" in text:
        return "5.2.1"
    if "System Reference Document 5.1" in text:
        return "5.1"
    raise parse.SrdError("This isn't the System Reference Document 5.2.1 or 5.1.")


def build(path: str) -> dict[str, dict[str, Any]]:
    """The data files' contents, by file name, from the PDF at `path` (5.2.1 or 5.1)."""
    pages = read_pages(path)
    if which_document(pages) == "5.1":
        document = {
            "title": "System Reference Document 5.1",
            "url": SOURCE_URL_51,
            "sha256": sha256_of(path),
            "pages": len(pages),
        }
        # The 5.1 words depend on the 5.2.1 data's words: record which data it was.
        document["word_list_sha256"] = word_list_sha256()
        monster_document = document | {"word_list_sha256": word_list_sha256(monsters_too=True)}
        return build_files_51(
            pages,
            document,
            known_words(),
            (monster_document, known_words(monsters_too=True), known_pairs(monsters_too=True)),
            known_pairs(),
        )
    document = {
        "title": "System Reference Document 5.2.1",
        "url": SOURCE_URL,
        "sha256": sha256_of(path),
        "pages": len(pages),
    }
    return build_files(pages, document)


def known_words(*, monsters_too: bool = False) -> set[str]:
    """The words of the 5.2.1 data: the 5.1 reader puts its cut words back using them. The
    5.2.1 data must be there, or the 5.1 files would come out differently. The monsters'
    words are included only for the 5.1 monsters, so the 5.1 spells don't depend on them."""
    words: set[str] = set()
    for name in _word_files(monsters_too):
        path = OUT / name
        if not path.exists():
            raise parse.SrdError(f"Build the SRD 5.2.1 data first: {path} is missing.")
        for entry in json.loads(path.read_text(encoding="utf-8"))["entries"]:
            for field in ("name", "text"):
                words.update(w.lower() for w in parse51.WORD.findall(entry[field]))
                words.update(w.lower() for w in parse51.HYPHENATED_WORD.findall(entry[field]))
    return words


def known_pairs(*, monsters_too: bool = False) -> set[tuple[str, str]]:
    """The neighbouring words of the 5.2.1 data (lower case): two words that are never side by
    side there may be one word cut in two (see `Vocabulary`)."""
    found: set[tuple[str, str]] = set()
    for name in _word_files(monsters_too):
        for entry in json.loads((OUT / name).read_text(encoding="utf-8"))["entries"]:
            words = [w.lower() for w in parse51.WORD.findall(entry["text"])]
            found.update(itertools.pairwise(words))
    return found


def _word_files(monsters_too: bool) -> tuple[str, ...]:
    return ("spells.json", "conditions.json") + (("monsters.json",) if monsters_too else ())


def word_list_sha256(*, monsters_too: bool = False) -> str:
    """A fingerprint of the 5.2.1 data files that `known_words()` reads."""
    digest = hashlib.sha256()
    for name in _word_files(monsters_too):
        digest.update((OUT / name).read_bytes())
    return digest.hexdigest()


def folder_for(files: dict[str, dict[str, Any]]) -> Path:
    """Where these files belong: the folder of their edition."""
    return OUT_51 if files["spells.json"]["edition"] == EDITION_51 else OUT


def build_files(
    pages: Sequence[Sequence[Line]], document: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The same from pages already read (so it can be tested without the PDF)."""
    spells_at = first_page_of(pages, SPELLS_HEADING, after=CONTENTS_PAGES)
    glossary_at = first_page_of(pages, GLOSSARY_HEADING, after=spells_at)
    toolbox_at = first_page_of(pages, AFTER_GLOSSARY_HEADING, after=glossary_at)
    spells = parse.parse_spells(lines_between(pages, spells_at, glossary_at))
    glossary = lines_between(pages, glossary_at, toolbox_at)
    conditions = parse.parse_conditions(glossary)
    rules = parse.parse_rules(glossary)
    after_toolbox_at = first_page_of(pages, AFTER_TOOLBOX_HEADING, after=toolbox_at)
    toolbox_lines = lines_between(pages, toolbox_at, after_toolbox_at)
    gameplay = toolbox.parse_toolbox(toolbox_lines)
    dropped = toolbox.left_out(toolbox_lines)
    if dropped != toolbox.EXPECTED_LEFT_OUT:
        raise parse.SrdError(f"The Gameplay Toolbox's left-out sections changed: {dropped}")
    classes_at = first_page_of(pages, CLASSES_HEADING, after=CONTENTS_PAGES)
    origins_at = first_page_of(pages, AFTER_CLASSES_HEADING, after=classes_at)
    features = classes.parse_features(lines_between(pages, classes_at, origins_at))

    feats_at = first_page_of(pages, FEATS_HEADING, after=origins_at)
    equipment_at = first_page_of(pages, AFTER_FEATS_HEADING, after=feats_at)
    origin_lines = lines_between(pages, origins_at, feats_at)
    species = origins.parse_species(origin_lines)
    backgrounds = origins.parse_backgrounds(origin_lines)
    feats = origins.parse_feats(lines_between(pages, feats_at, equipment_at))
    weapons_at = first_page_of(pages, WEAPONS_HEADING, after=equipment_at)
    armor_at = first_page_of(pages, ARMOR_HEADING, after=weapons_at)
    tools_at = first_page_of(pages, AFTER_ARMOR_HEADING, after=armor_at)
    properties = equipment.parse_properties(lines_between(pages, equipment_at, weapons_at))
    weapons = equipment.parse_weapons(lines_between(pages, weapons_at, armor_at))
    armors = equipment.parse_armor(lines_between(pages, armor_at, tools_at))
    tool_lines, gear_lines, mount_lines = equipment_lines(pages, tools_at)
    tools = gear.parse_tools(tool_lines)
    adventuring_gear = gear.parse_gear(gear_lines)
    mounts_and_vehicles = gear.parse_mounts(mount_lines)
    items_at = first_page_of(pages, ITEMS_HEADING, after=CONTENTS_PAGES)
    after_items_at = first_page_of(pages, AFTER_ITEMS_HEADING, after=items_at)
    magic_items = items.parse_items(lines_between(pages, items_at, after_items_at))

    monsters_at = first_page_of(pages, MONSTERS_HEADING, after=CONTENTS_PAGES)
    creatures = monsters.parse_monsters(lines_between(pages, monsters_at, len(pages) + 1))

    def header(kind: str, section: str | None) -> dict[str, Any]:
        head: dict[str, Any] = {
            "source": SOURCE,
            "edition": EDITION,
            "licence": LICENCE,
            "document": document,
            "kind": kind,
        }
        return head | ({"section": section} if section else {})

    return {
        "spells.json": {
            **header("spell", SPELLS_HEADING),
            "entries": [asdict(s) | {"classes": list(s.classes)} for s in spells],
        },
        "conditions.json": {
            **header("condition", GLOSSARY_HEADING),
            "entries": [asdict(c) for c in conditions],
        },
        "rules.json": {
            **header("rule", GLOSSARY_HEADING),
            "entries": [asdict(r) | {"see_also": list(r.see_also)} for r in rules],
        },
        # Four kinds in one file: each entry says its own.
        "toolbox.json": {
            **header("toolbox", TOOLBOX_SECTION),
            "entries": [
                *({"kind": "rule", **asdict(r) | {"see_also": []}} for r in gameplay.rules),
                # The Magic Items rules say their own section (the players' are not the DM's).
                *({"kind": "rule", **asdict(m) | {"see_also": []}} for m in gameplay.magic),
                *({"kind": "poison", **asdict(p)} for p in gameplay.poisons),
                *({"kind": "trap", **asdict(t)} for t in gameplay.traps),
                *({"kind": "curse", **asdict(c)} for c in gameplay.curses),
            ],
        },
        "features.json": {
            **header("feature", CLASSES_HEADING),
            "entries": [feature_entry(f) for f in features],
        },
        "feats.json": {
            **header("feat", FEATS_HEADING),
            "entries": [asdict(f) for f in feats],
        },
        # Two kinds in one file (the species and the backgrounds of the chapter): each entry
        # says its own.
        "origins.json": {
            **header("origin", ORIGINS_SECTION),
            "entries": [
                *({"kind": "species", **asdict(s)} for s in species),
                *({"kind": "background", **asdict(b)} for b in backgrounds),
            ],
        },
        # Three kinds in one file: each entry says its own (and the properties their section).
        "equipment.json": {
            **header("equipment", EQUIPMENT_SECTION),
            "entries": [
                *(equipment_entry("weapon", w) for w in weapons),
                *(equipment_entry("armor", a) for a in armors),
                *(equipment_entry("rule", p) for p in properties),
                *(tool_entry(t) for t in tools),
                *(gear_entry(g) for g in (*adventuring_gear, *mounts_and_vehicles)),
            ],
        },
        "items.json": {
            **header("item", ITEMS_SECTION),
            "entries": [item_entry(i) for i in magic_items],
        },
        "monsters.json": {
            **header("monster", None),
            "entries": [monster_entry(m, EDITION) for m in creatures],
        },
    }


def title_index(lines: Sequence[Line], title: str, start: int = 0) -> int:
    """The index of the first title line with exactly these words, at or after `start`."""
    for i in range(start, len(lines)):
        if parse.is_title(lines[i]) and parse._clean(lines[i].text) == title:
            return i
    raise parse.SrdError(f"Can't find the heading {title!r}: is this the SRD it should be?")


def equipment_lines(
    pages: Sequence[Sequence[Line]], tools_at: int
) -> tuple[list[Line], list[Line], list[Line]]:
    """The lines of the tools, of the adventuring gear and of the mounts and vehicles. The next
    section (lifestyle expenses) starts in the middle of a page, and the right-hand cells of the
    vehicles table come after it in reading order, so the mounts' lines run to the page's end
    (the reader ignores everything that is not a table row)."""
    after = next(
        n
        for n in range(tools_at + 1, len(pages) + 1)
        if any(
            parse.is_title(ln) and parse._clean(ln.text) == LIFESTYLE_HEADING for ln in pages[n - 1]
        )
    )
    lines = lines_between(pages, tools_at, after + 1)
    gear_at = title_index(lines, GEAR_HEADING_52)
    mounts_at = title_index(lines, MOUNTS_HEADING, gear_at)
    tools_from = title_index(lines, "Artisan’s Tools")
    return lines[tools_from:gear_at], lines[gear_at:mounts_at], lines[mounts_at:]


def tool_entry(tool: gear.Tool) -> dict[str, Any]:
    """A tool as the equipment file holds it: its fields, and its Utilize, Craft and Variants
    lines as printed (the ability, weight and cost are in the fields)."""
    lines = [tool.text] if tool.text else []
    if tool.utilize:
        lines.append(f"Utilize: {tool.utilize}")
    if tool.craft:
        lines.append(f"Craft: {tool.craft}")
    if tool.variants:
        lines.append(f"Variants: {tool.variants}")
    return {"kind": "tool", **asdict(tool), "text": "\n".join(lines)}


def gear_entry(entry: gear.Gear) -> dict[str, Any]:
    """An adventuring gear entry, a mount or a vehicle. A mount's or a vehicle's table row is read
    out as words (it has none of its own); a gear entry has its printed words."""
    fields = dict(entry.extra)
    if entry.group == "Mount":
        speed = f"Speed {fields['speed'].removesuffix('.')}. " if "speed" in fields else ""
        text = f"{speed}Carrying capacity {fields['capacity']}"
    elif entry.group == "Vehicle" and "speed" in fields:
        text = _ship_words(fields)
    elif entry.group == "Vehicle":
        text = f"Weighs {entry.weight}"
    else:
        text = entry.text
    data = {k: v for k, v in asdict(entry).items() if k != "extra"}
    return {"kind": "gear", **data, **fields, "text": text}


def _ship_words(fields: dict[str, str]) -> str:
    """A ship's row as words; a cell with a dash (it has none) is left out."""
    parts = [f"Speed {fields['speed']}."]
    if "ac" not in fields:  # the SRD 5.1 gives a ship its speed and price only
        return parts[0]
    for key, label in (("crew", "Crew"), ("passengers", "Passengers")):
        if fields[key] != "—":
            parts.append(f"{label} {fields[key]}.")
    if fields["cargo"] != "—":
        tons = "ton" if fields["cargo"] in ("1", "1/2") else "tons"
        parts.append(f"Cargo {fields['cargo']} {tons}.")
    parts.append(f"AC {fields['ac']}. HP {fields['hp']}.")
    if fields["damage_threshold"] != "—":
        parts.append(f"Damage threshold {fields['damage_threshold']}.")
    return " ".join(parts)


def _weight_and_cost(entry: Any) -> str:
    """ "3 lb., 15 GP." (a weight of "—" is left out: there is none to say)."""
    parts = [entry.weight if entry.weight != "—" else "", entry.cost]
    return ", ".join(p for p in parts if p) + "."


def equipment_entry(kind: str, entry: Any, note: str = "") -> dict[str, Any]:
    """A weapon, an armor or a weapon property as the equipment file holds it: its kind, name and
    the fields of its table row, then the row read out as words (and the `note` the SRD 5.1 has on
    the Lance and the Net), and its page. The SRD 5.1 has no weapon mastery, so it says none."""
    if kind == "weapon":  # the number a DM wants first; the heading already says the category
        parts = [f"{entry.damage} {entry.damage_type}."] if entry.damage else []
        if entry.properties:
            parts.append(f"Properties: {entry.properties}.")
        if entry.mastery:
            parts.append(f"Mastery: {entry.mastery}.")
        parts.append(_weight_and_cost(entry))
        if note:
            parts.append(note)
        return {"kind": kind, **asdict(entry), "text": " ".join(parts)}
    if kind == "armor":
        parts = [f"AC {entry.ac}."]
        if entry.strength != "—":
            parts.append(f"Needs {entry.strength.replace('Str ', 'Strength ')}.")
        if entry.stealth != "—":
            parts.append(f"{entry.stealth} on Stealth.")
        parts.append(_weight_and_cost(entry))
        if entry.category == "Shield" and entry.don_doff.startswith("Utilize"):
            parts.append("Use the Utilize action to don or doff.")
        else:
            parts.append(f"Takes {entry.don_doff.lower()}.")
        return {"kind": kind, **asdict(entry), "text": " ".join(parts)}
    return {
        "kind": "rule",
        "section": EQUIPMENT_SECTION,
        "name": entry.name,
        "tag": entry.tag,
        "text": entry.text,
        "page": entry.page,
        "see_also": [],
    }


def item_entry(item: items.Item) -> dict[str, Any]:
    """A magic item as a data file holds it: its name, then what the line under it says
    (category, the category's bracket as subtype, rarity as printed, attunement and for whom),
    then its text and page."""
    return {
        "name": item.name,
        "category": item.category,
        "subtype": item.subtype,
        "rarity": item.rarity,
        "attunement": item.attunement,
        "attunement_by": item.attunement_by,
        "text": item.text,
        "page": item.page,
    }


def feature_entry(feature: classes.Feature) -> dict[str, Any]:
    """A class feature as a data file holds it: its name, its class (and subclass when it
    belongs to one) and level, then its text and page."""
    return {
        "name": feature.name,
        "class": feature.klass,
        "level": feature.level,
        "subclass": feature.subclass,
        "text": feature.text,
        "page": feature.page,
    }


def monster_entry(monster: monsters.Monster, edition: str) -> dict[str, Any]:
    """A monster as a data file holds it: its name, section and page, the fields a rules
    check needs (the six ability scores each on their own), then its text."""
    values = asdict(monster) | {name: score for name, score, _ in monster.abilities}
    keys = (*MONSTER_FIELDS, *MONSTER_FIELDS_BY_EDITION[edition])
    return {
        "name": monster.name,
        "section": monster.section,
        "page": monster.page,
        **{key: values[key] for key in keys},
        "text": monster.text,
    }


def class_lines_51(pages: Sequence[Sequence[Line]]) -> list[Line]:
    """The lines of the 5.1's Classes chapter: from the page that starts with the first class to
    the heading of the chapter after it, which starts in the middle of a page."""
    first = first_page_of(pages, CLASSES_HEADING_51)
    lines = lines_between(pages, first, len(pages) + 1)
    for i, line in enumerate(lines):
        if parse.is_title(line) and parse51.clean(line.text) == AFTER_CLASSES_HEADING_51:
            return lines[:i]
    raise parse.SrdError(f"Can't find the heading {AFTER_CLASSES_HEADING_51!r} after the classes.")


def item_lines_51(lines: Sequence[Line]) -> list[Line]:
    """The Magic Items chapter's lines up to its Sentient Magic Items section (which is rules
    about giving an item a mind, not an item; the Artifacts after it are left out too)."""
    for i, line in enumerate(lines):
        if parse.is_title(line) and parse51.clean(line.text) == SENTIENT_HEADING_51:
            return list(lines[:i])
    raise parse.SrdError(f"Can't find the heading {SENTIENT_HEADING_51!r} after the items.")


GM_HEADING_51 = "Traps"
AFTER_GM_HEADING_51 = "Magic Items"
RULES_HEADING_51 = "Using Ability"  # (the chapter's title wraps; its first line starts the page)
AFTER_RULES_HEADING_51 = "Spell Lists"


def title_position(lines: Sequence[Line], title: str, start: int = 0) -> int:
    """The index of the first title line with exactly these words, at or after `start`."""
    for i in range(start, len(lines)):
        if parse.is_title(lines[i]) and parse51.clean(lines[i].text) == title:
            return i
    raise parse.SrdError(f"Can't find the heading {title!r}: is this the SRD it should be?")


def page_with_title(pages: Sequence[Sequence[Line]], title: str, after: int = 0) -> int:
    """The number of the first page, after page `after`, with a title of exactly these words
    anywhere on it (a chapter can start in the middle of a page)."""
    for number, lines in enumerate(pages, 1):
        if number > after and any(
            parse.is_title(ln) and parse51.clean(ln.text) == title for ln in lines
        ):
            return number
    raise parse.SrdError(f"Can't find the heading {title!r}: is this the SRD it should be?")


def origin_files_51(
    pages: Sequence[Sequence[Line]], header: Any, vocab: parse51.Vocabulary
) -> dict[str, dict[str, Any]]:
    """The 5.1's races, its Acolyte background and its Grappler feat as `origins.json` and
    `feats.json`, in the shapes of the 5.2.1's."""
    races_at = page_with_title(pages, RACES_HEADING_51)
    after_races_at = first_page_of(pages, AFTER_RACES_HEADING_51, after=races_at)
    races = origins51.parse_races(lines_between(pages, races_at, after_races_at), vocab)
    backgrounds_at = page_with_title(pages, BACKGROUNDS_HEADING_51, after=after_races_at)
    lines = lines_between(pages, backgrounds_at, backgrounds_at + 2)
    start = title_position(lines, BACKGROUND_51)
    background = origins51.parse_background(
        lines[start : title_position(lines, AFTER_BACKGROUND_HEADING_51, start)], vocab
    )
    feat_at = page_with_title(pages, FEAT_51, after=backgrounds_at)
    lines = lines_between(pages, feat_at, feat_at + 1)
    feat = origins51.parse_feat(lines[title_position(lines, FEAT_51) :], vocab)
    return {
        "origins.json": {
            **header("origin", "Races and Backgrounds"),
            "entries": [
                *(race_entry(r) for r in races),
                {"kind": "background", "section": "Backgrounds", **asdict(background)}
                | {"ability_scores": "", "feat": "", "tool": ""},
            ],
        },
        "feats.json": {
            **header("feat", "Feats"),
            "entries": [{"category": "", **asdict(feat)}],
        },
    }


def rules_files_51(
    pages: Sequence[Sequence[Line]], header: Any, vocab: parse51.Vocabulary
) -> dict[str, dict[str, Any]]:
    """The 5.1's core rules chapters (Using Ability Scores, Adventuring, Combat and Spellcasting up
    to the spell lists) as `rules.json`, in the shape of the 5.2.1's glossary (#1943)."""
    first = first_page_of(pages, RULES_HEADING_51, after=CONTENTS_PAGES)
    last = first_page_of(pages, AFTER_RULES_HEADING_51, after=first)
    lines = lines_between(pages, first, last)
    found = rules51.parse_rules(lines, vocab)
    dropped = rules51.left_out(lines)
    if dropped != rules51.EXPECTED_LEFT_OUT:
        raise parse.SrdError(f"The 5.1's rules chapters leave out something new: {dropped}")
    return {
        "rules.json": {
            **header("rule", rules51.SECTION),
            "entries": [asdict(r) | {"see_also": []} for r in found],
        }
    }


def gm_files_51(
    pages: Sequence[Sequence[Line]], header: Any, vocab: parse51.Vocabulary
) -> dict[str, dict[str, Any]]:
    """The 5.1's game master chapters (Traps, Diseases, Madness, Objects, Poisons) as
    `toolbox.json`, in the kinds and section of the 5.2.1's (#1944)."""
    first = first_page_of(pages, GM_HEADING_51, after=CONTENTS_PAGES)
    last = first_page_of(pages, AFTER_GM_HEADING_51, after=first)
    lines = lines_between(pages, first, last)
    found = gm51.parse_gm(lines, vocab)
    dropped = gm51.left_out(lines)
    if dropped != gm51.EXPECTED_LEFT_OUT:
        raise parse.SrdError(f"The 5.1's game master chapters leave out something new: {dropped}")
    return {
        "toolbox.json": {
            **header("toolbox", gm51.SECTION),
            "entries": [
                *({"kind": "rule", **asdict(r) | {"see_also": []}} for r in found.rules),
                *({"kind": "trap", **asdict(t)} for t in found.traps),
                *({"kind": "disease", **asdict(d)} for d in found.diseases),
                *({"kind": "poison", **asdict(p)} for p in found.poisons),
            ],
        }
    }


def equipment_files_51(
    pages: Sequence[Sequence[Line]], header: Any, vocab: parse51.Vocabulary
) -> dict[str, dict[str, Any]]:
    """The 5.1's Equipment chapter as `equipment.json`, in the 5.2.1 file's kinds and shapes. The
    tables are read by page (their right-hand cells come after the page's left column in reading
    order), the paragraphs by the titles around them."""
    first = first_page_of(pages, EQUIPMENT_HEADING_51, after=BACKGROUND_PAGES_51)
    last = page_with_title(pages, TRADE_GOODS_HEADING_51, after=first)
    lines = lines_between(pages, first, last + 1)

    def at(title: str, start: int = 0) -> int:
        return title_position(lines, title, start)

    def table(number: int, keep: Any = None) -> list[Line]:
        return [
            ln
            for ln in pages[number - 1]
            if ln.first_font.startswith("Calibri") and (keep is None or keep(ln))
        ]

    def page(title: str) -> int:
        return page_with_title(pages, title, after=first - 1)

    armor_at, props_at = page("Light Armor"), page("Weapon Properties")
    packs_at, mounts_at = page("Equipment Packs"), page(MOUNTS_TITLE_51)
    special_at = at("Special Weapons")
    properties = equipment51.parse_properties(
        lines[at("Weapon Properties") : at("Improvised Weapons")], vocab
    )
    notes = equipment51.parse_special_weapons(
        lines[special_at : at("Adventuring Gear", special_at)], vocab
    )
    weapons = equipment51.parse_weapons(
        equipment51.rows_of(table(props_at + 1) + table(props_at + 2))
    )
    armors = equipment51.parse_armor(equipment51.rows_of(table(armor_at) + table(armor_at + 1)))
    gear_rows = equipment51.rows_of(
        table(packs_at - 1, lambda ln: not (ln.x >= HALF and ln.y <= CONTAINERS_BELOW)),
        columns=True,
    )
    gear_words = lines[at("Adventuring Gear", special_at) : at("Equipment Packs")]
    gear = equipment51.parse_gear(
        gear_rows, [ln for ln in gear_words if not ln.first_font.startswith("Calibri")], vocab
    )
    tool_rows = equipment51.rows_of(table(packs_at, lambda ln: ln.x >= HALF), columns=True)
    tools = equipment51.parse_tools(
        [r for r in tool_rows if not r.text.startswith("*")],
        lines[at("Tools", at("Equipment Packs")) : at(MOUNTS_TITLE_51)],
        vocab,
    )
    mount_rows = equipment51.rows_of(
        table(mounts_at) + table(mounts_at + 1, lambda ln: ln.x < HALF), columns=True
    )
    mounts = equipment51.parse_mounts(mount_rows)
    entries = [
        *(equipment_entry("weapon", w, notes.get(w.name, "")) for w in weapons),
        *(equipment_entry("armor", a) for a in armors),
        *(equipment_entry("rule", p) for p in properties),
        *(tool_entry(t) for t in tools),
        *(gear_entry(g) for g in (*gear, *mounts)),
    ]
    return {"equipment.json": {**header("equipment", EQUIPMENT_SECTION), "entries": entries}}


def race_entry(race: origins51.Race) -> dict[str, Any]:
    """A race as the origins file holds it: a `species`, in the 5.2.1's shape (the SRD 5.1 does
    not say a race's creature type, so that is empty)."""
    return {
        "kind": "species",
        "section": "Races",
        "name": race.name,
        "creature_type": "",
        "size": race.size,
        "speed": race.speed,
        "text": race.text,
        "page": race.page,
    }


def build_files_51(
    pages: Sequence[Sequence[Line]],
    document: dict[str, Any],
    known: set[str],
    monster_input: tuple[dict[str, Any], set[str], set[tuple[str, str]]] | None = None,
    pairs: set[tuple[str, str]] | None = None,
    *,
    classes: bool = True,
    items_pages: bool = True,
    origins_pages: bool = True,
    rules_pages: bool = True,
) -> dict[str, dict[str, Any]]:
    """The 2014 SRD 5.1's files from pages already read. `classes=False`, `items_pages=False` and
    `origins_pages=False` and `rules_pages=False` leave out the class features, the magic items,
    the races, background and feat, and the core rules, for tests whose pages hold no such
    chapter."""
    spells_at = first_page_of(pages, SPELLS_HEADING_51, after=CONTENTS_PAGES)
    traps_at = first_page_of(pages, AFTER_SPELLS_HEADING_51, after=spells_at)
    conditions_at = first_page_of(pages, CONDITIONS_HEADING_51, after=traps_at)
    pantheons_at = first_page_of(pages, AFTER_CONDITIONS_HEADING_51, after=conditions_at)
    spell_lines = lines_between(pages, spells_at, traps_at)
    condition_lines = lines_between(pages, conditions_at, pantheons_at)
    # Every page of the PDF teaches it which words are words (the book says "her" often).
    vocab = parse51.Vocabulary(known, [line for page in pages for line in page], pairs or ())
    spells = parse51.parse_spells(spell_lines, vocab)
    conditions = parse51.parse_conditions(condition_lines, vocab, CONDITIONS_51)

    def header(kind: str, section: str | None, doc: dict[str, Any] = document) -> dict[str, Any]:
        head: dict[str, Any] = {
            "source": SOURCE_51,
            "edition": EDITION_51,
            "licence": LICENCE,
            "document": doc,
            "kind": kind,
        }
        return head | ({"section": section} if section else {})

    files = {
        "spells.json": {
            **header("spell", SPELLS_HEADING_51),
            "entries": [asdict(s) for s in spells],
        },
        "conditions.json": {
            **header("condition", "Appendix PH-A: Conditions"),
            "entries": [asdict(c) for c in conditions],
        },
    }
    if classes:
        features = classes51.parse_features51(class_lines_51(pages), vocab)
        files["features.json"] = {
            **header("feature", CLASSES_SECTION),
            "entries": [feature_entry(f) for f in features],
        }
    if items_pages:
        items_at = first_page_of(pages, ITEMS_HEADING_51, after=CONTENTS_PAGES)
        after_items_at = first_page_of(pages, AFTER_ITEMS_HEADING_51, after=items_at)
        files["items.json"] = {
            **header("item", ITEMS_SECTION),
            "entries": [
                item_entry(i)
                for i in items51.parse_items(
                    item_lines_51(lines_between(pages, items_at, after_items_at)), vocab
                )
            ],
        }
    if origins_pages:
        files.update(origin_files_51(pages, header, vocab))
        files.update(equipment_files_51(pages, header, vocab))
    if rules_pages:
        files.update(rules_files_51(pages, header, vocab))
        files.update(gm_files_51(pages, header, vocab))
    if monster_input is not None:
        monster_document, monster_known, monster_pairs = monster_input
        everything = [line for page in pages for line in page]
        monster_vocab = parse51.Vocabulary(monster_known, everything, monster_pairs)
        monsters_at = first_page_of(pages, MONSTERS_HEADING_51, after=CONTENTS_PAGES)
        sections = [(monsters_at, "Monsters")]
        lines = lines_between(pages, monsters_at, conditions_at)
        for heading, name in MONSTER_APPENDICES_51:
            at = first_page_of(pages, heading, after=pantheons_at)
            sections.append((at, name))
        appendices_at = sections[1][0]  # the two appendices follow each other to the end
        lines += lines_between(pages, appendices_at, len(pages) + 1)
        creatures = monsters51.parse_monsters(lines, sections, monster_vocab)
        files["monsters.json"] = {
            **header("monster", None, monster_document),
            "entries": [monster_entry(m, EDITION_51) for m in creatures],
        }
    return files


def dump(data: dict[str, Any]) -> str:
    """The file's text: stable, readable in review, with the words as they are."""
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def write(files: dict[str, dict[str, Any]], out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        target = out / name
        target.write_text(dump(data), encoding="utf-8")
        written.append(target)
    return written
