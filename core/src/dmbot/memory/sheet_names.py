"""Names a sheet can be checked against (#1124; docs/PLAN.md, "Character sheets, part D").

The rules index (`dmbot.rules.index`) knows spells and conditions of the SRD. It has no
classes, species or feats, so this module keeps those names, and nothing but names: no
description, no rules text. Each name is in one of two groups:

- **SRD:** in the free rules DMbot ships (SRD 5.2.1 and 5.1). A sheet that uses it is checked.
- **Official, not in the SRD:** a real book's name that DMbot has no rules for. It may be
  perfectly fine, so a sheet that uses it gets a calm warning, not an error.

Anything in neither group is unknown (homebrew, or a typo). `kind` is "class", "species" or
"feat". Lookups ignore case, punctuation and spacing, as the rules index does.
"""

from __future__ import annotations

import difflib

from dmbot.rules.index import normalize

SRD: dict[str, tuple[str, ...]] = {
    "class": (
        "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue",
        "Sorcerer", "Warlock", "Wizard",
    ),
    "species": (
        "Dragonborn", "Dwarf", "Elf", "Gnome", "Goliath", "Halfling", "Human", "Orc", "Tiefling",
        "Half-Elf", "Half-Orc", "Hill Dwarf", "High Elf", "Lightfoot Halfling", "Rock Gnome",
    ),
    "feat": (
        "Ability Score Improvement", "Alert", "Archery", "Defense", "Grappler",
        "Great Weapon Fighting", "Magic Initiate", "Savage Attacker", "Skilled",
        "Two-Weapon Fighting", "Boon of Combat Prowess", "Boon of Dimensional Travel",
        "Boon of Fate", "Boon of Irresistible Offense", "Boon of Spell Recall",
        "Boon of the Night Spirit", "Boon of Skill", "Boon of Speed", "Boon of Energy Resistance",
        "Boon of Fortitude", "Boon of Recovery", "Boon of Truesight",
    ),
}  # fmt: skip
OFFICIAL: dict[str, tuple[str, ...]] = {
    "class": ("Artificer",),
    "species": (
        "Aasimar", "Aarakocra", "Autognome", "Astral Elf", "Bugbear", "Centaur", "Changeling",
        "Eladrin", "Fairy", "Firbolg", "Genasi", "Giff", "Gith", "Githyanki", "Githzerai",
        "Goblin", "Grung", "Hadozee", "Harengon", "Hexblood", "Hobgoblin", "Kalashtar", "Kenku",
        "Kobold", "Leonin", "Lizardfolk", "Loxodon", "Minotaur", "Orc of Exandria", "Owlin",
        "Plasmoid", "Reborn", "Satyr", "Shadar-kai", "Shifter", "Simic Hybrid", "Tabaxi",
        "Thri-kreen", "Tortle", "Triton", "Vedalken", "Verdan", "Warforged", "Yuan-ti Pureblood",
    ),
    "feat": (
        "Actor", "Athlete", "Charger", "Chef", "Crossbow Expert", "Crusher", "Defensive Duelist",
        "Dual Wielder", "Dungeon Delver", "Durable", "Elemental Adept", "Eldritch Adept",
        "Fey Touched", "Fighting Initiate", "Gunner", "Great Weapon Master", "Healer",
        "Heavily Armored", "Heavy Armor Master", "Inspiring Leader", "Keen Mind", "Lightly Armored",
        "Linguist", "Lucky", "Mage Slayer", "Martial Adept", "Medium Armor Master",
        "Metamagic Adept", "Mobile", "Moderately Armored", "Mounted Combatant", "Observant",
        "Piercer", "Poisoner", "Polearm Master", "Resilient", "Ritual Caster", "Sentinel",
        "Shadow Touched", "Sharpshooter", "Shield Master", "Skill Expert", "Skulker", "Slasher",
        "Speedy", "Spell Sniper", "Tavern Brawler", "Telekinetic", "Telepathic", "Tough",
        "War Caster", "Weapon Master",
    ),
}  # fmt: skip

_KEYS = {
    kind: {
        "srd": {normalize(n) for n in SRD[kind]},
        "official": {normalize(n) for n in OFFICIAL[kind]},
    }
    for kind in SRD
}


def where(kind: str, name: str) -> str:
    """ "srd" if the free rules have it, "official" if it is a real book's name DMbot has no
    rules for, else "unknown". A species like "Hill Dwarf" counts if a word of it is a
    species DMbot knows ("Dwarf")."""
    key = normalize(name)
    groups = _KEYS[kind]
    if key in groups["srd"]:
        return "srd"
    if key in groups["official"]:
        return "official"
    if kind == "species":
        words = set(key.split())
        if words & groups["srd"]:
            return "srd"
        if words & groups["official"]:
            return "official"
    return "unknown"


def close(kind: str, name: str) -> str | None:
    """A name that is nearly the one given ("Sharpshoter" for Sharpshooter), or None. Only to
    say what it might be: nothing is replaced."""
    key = normalize(name)
    pool = {normalize(n): n for n in (*SRD[kind], *OFFICIAL[kind])}
    found = difflib.get_close_matches(key, list(pool), n=1, cutoff=0.75)
    return pool[found[0]] if found else None
