"""The player's view of their own sheet, section by section, and the edit forms behind it
(#1126; docs/PLAN.md, "The player's sheet view"). Pure: no Discord, no database.

A section is one part of the snapshot (`sheets`): how to show it as plain text, and, for a
sheet the player typed or gave as a document, the fields of its edit form and how a filled-in
form becomes new values. Every edited snapshot goes back through `sheets.clean`, so an edit can
never store anything an import couldn't (names and numbers, the right shapes and sizes), and
a mistake in a form is said in one plain line before anything is saved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dmbot.memory import sheets
from dmbot.memory.sheet_checks import class_line, vitals

SHOWN = 25  # names listed in a section before "and N more"
FIELD_MAX = 4000  # characters in one form box: Discord's own limit, so a long list fits


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    label: str  # 45 characters at most (Discord)
    placeholder: str = ""
    long: bool = False  # a paragraph box
    names: bool = False  # a list of names (cut only between names, checked for length)


def skill_name(key: str) -> str:
    """ "sleight-of-hand" as a player writes it: "Sleight of Hand"."""
    return " ".join(w if w == "of" else w.capitalize() for w in key.split("-"))


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    label: str  # the heading
    show: Callable[[Mapping[str, Any]], str]
    fields: tuple[Field, ...]
    # (snapshot, {field key: text}) -> (changes to the snapshot, a problem in plain words).
    apply: Callable[[Mapping[str, Any], Mapping[str, str]], tuple[dict[str, Any], str | None]]
    fill: Callable[[Mapping[str, Any]], dict[str, str]]  # what each field holds now
    short: str = ""  # a shorter button label, where the heading is too long for a phone

    @property
    def button(self) -> str:
        return self.short or self.label


def _list(snapshot: Mapping[str, Any], key: str) -> list[str]:
    value = snapshot.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _few(names: list[str]) -> str:
    head = ", ".join(names[:SHOWN])
    return f"{head} and {len(names) - SHOWN} more" if len(names) > SHOWN else head


def split_names(text: str) -> list[str]:
    """Names from a form box: commas, semicolons or lines."""
    return [part.strip() for part in re.split(r"[,;\n]", text) if part.strip()]


def fit(text: str) -> tuple[str, list[str]]:
    """(what a form box can hold, the names left out). A list is cut between names, never in
    the middle of one, and the names left out are kept by the caller and added back on submit,
    so pressing Submit on an unchanged form changes nothing (#1126)."""
    if len(text) <= FIELD_MAX:
        return text, []
    names = split_names(text)
    shown: list[str] = []
    size = 0
    for i, name in enumerate(names):
        size += len(name) + 2
        if size > FIELD_MAX:
            return ", ".join(shown), names[i:]
        shown.append(name)
    return ", ".join(shown), []


def _int(text: str, low: int, high: int, what: str) -> tuple[int | None, str | None]:
    raw = text.strip()
    if not raw:
        return None, None
    if re.fullmatch(r"-?\d+", raw) is None:
        return None, f"{what}: type digits only, like 30."
    number = int(raw)
    if not low <= number <= high:
        return None, f"{what} should be from {low} to {high}."
    return number, None


# ---- showing -------------------------------------------------------------------------


def basic_line(snapshot: Mapping[str, Any]) -> str:
    """ "Belleros, Ranger 3 + Druid 3, HP 11 / 26 + 0, AC 18": the top of the view."""
    return ", ".join(p for p in (snapshot.get("name"), class_line(snapshot), vitals(snapshot)) if p)


def _modifier(score: int) -> str:
    mod = (score - 10) // 2
    return f"+{mod}" if mod >= 0 else str(mod)


def _show_abilities(s: Mapping[str, Any]) -> str:
    scores = s.get("abilities") if isinstance(s.get("abilities"), Mapping) else {}
    assert isinstance(scores, Mapping)
    if not scores:
        return "No ability scores yet."
    line = "  ".join(
        f"{a.upper()} {scores[a]} ({_modifier(scores[a])})" for a in sheets.ABILITIES if a in scores
    )
    saves = [a.upper() for a in _list(s, "saves")]
    bonus = s.get("proficiency_bonus")
    extra = [
        f"Saving throws: {', '.join(saves)}" if saves else "",
        f"Proficiency bonus +{bonus}" if bonus else "",
    ]
    return "\n".join(p for p in (line, *extra) if p)


def _show_conditions(s: Mapping[str, Any]) -> str:
    names = _list(s, "conditions")
    exhaustion = s.get("exhaustion") or 0
    shown = [f"Exhaustion {exhaustion}" if n == "Exhaustion" and exhaustion else n for n in names]
    return ", ".join(shown) if shown else "None right now."


def _show_defenses(s: Mapping[str, Any]) -> str:
    rows = [
        (label, _list(s, key))
        for label, key in (
            ("Resists", "resistances"),
            ("Immune to", "immunities"),
            ("Weak to", "vulnerabilities"),
        )
    ]
    shown = [f"{label}: {_few(names)}" for label, names in rows if names]
    return "\n".join(shown) if shown else "None."


def _show_skills(s: Mapping[str, Any]) -> str:
    skills, expert = _list(s, "skills"), _list(s, "expertise")
    if not skills:
        return "No trained skills yet."
    shown = ", ".join(skill_name(k) for k in skills)
    return f"Trained: {shown}" + (
        f"\nExpert: {', '.join(skill_name(k) for k in expert)}" if expert else ""
    )


def _show_proficiencies(s: Mapping[str, Any]) -> str:
    rows = [
        (label, _list(s, key))
        for label, key in (
            ("Tools", "tools"),
            ("Weapons", "weapon_profs"),
            ("Armor", "armor_profs"),
        )
    ]
    shown = [f"{label}: {_few(names)}" for label, names in rows if names]
    return "\n".join(shown) if shown else "None yet."


def _show_features(s: Mapping[str, Any]) -> str:
    features, feats = _list(s, "features"), _list(s, "feats")
    rows = [
        f"Features: {_few(features)}" if features else "",
        f"Feats: {_few(feats)}" if feats else "",
    ]
    return "\n".join(r for r in rows if r) or "None yet."


def _show_background(s: Mapping[str, Any]) -> str:
    return str(s["background"]) if s.get("background") else "No background yet."


def _show_speed(s: Mapping[str, Any]) -> str:
    senses = s.get("senses") if isinstance(s.get("senses"), Mapping) else {}
    assert isinstance(senses, Mapping)
    rows = [f"Speed {s['speed']} ft" if isinstance(s.get("speed"), int) else ""]
    rows += [f"{name.capitalize()} {feet} ft" for name, feet in senses.items()]
    return "\n".join(r for r in rows if r) or "Nothing yet."


def _show_languages(s: Mapping[str, Any]) -> str:
    return _few(_list(s, "languages")) or "None yet."


def _show_inventory(s: Mapping[str, Any]) -> str:
    inv = s.get("inventory") if isinstance(s.get("inventory"), Mapping) else None
    items = _list(s, "items")
    if inv is None and not items:
        return "Nothing yet."
    rows = []
    if inv is not None:
        rarity = inv.get("rarity") if isinstance(inv.get("rarity"), Mapping) else {}
        counts = [f"{rarity[r]} {r}" for r in reversed(sheets.RARITIES) if rarity.get(r)]
        rows.append(
            f"{inv.get('count', 0)} items, {inv.get('weight', 0):g} lb"
            + (f": {', '.join(counts)}" if counts else "")
        )
    if items:
        rows.append(f"Items: {_few(items)}")
    return "\n".join(rows)


# ---- editing -------------------------------------------------------------------------


def _fill_abilities(s: Mapping[str, Any]) -> dict[str, str]:
    scores = s.get("abilities") if isinstance(s.get("abilities"), Mapping) else {}
    assert isinstance(scores, Mapping)
    return {
        "scores": " ".join(str(scores[a]) for a in sheets.ABILITIES) if len(scores) == 6 else ""
    }


def _apply_abilities(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    words = re.split(r"[\s,;]+", v["scores"].strip())
    if len(words) != 6 or not all(w.isdecimal() for w in words):
        return (
            {},
            "Type six numbers in this order: STR DEX CON INT WIS CHA. Example: 10 14 12 8 10 13.",
        )
    numbers = [int(w) for w in words]
    if not all(1 <= n <= sheets.SCORE_MAX for n in numbers):
        return {}, f"Each score should be a whole number from 1 to {sheets.SCORE_MAX}."
    return {"abilities": dict(zip(sheets.ABILITIES, numbers, strict=True))}, None


def _fill_conditions(s: Mapping[str, Any]) -> dict[str, str]:
    names = [n for n in _list(s, "conditions") if n != "Exhaustion"]
    return {"names": ", ".join(names), "exhaustion": str(s.get("exhaustion") or 0)}


def _apply_conditions(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    known = {c.casefold(): c for c in sheets.CONDITIONS}
    names: list[str] = []
    unknown: list[str] = []
    for name in split_names(v["names"]):
        found = known.get(name.casefold())
        (names if found else unknown).append(found or name)
    if unknown:
        return (
            {},
            f"'{unknown[0]}' isn't a condition DMbot knows. Use names like Poisoned or Prone.",
        )
    level, problem = _int(v["exhaustion"], 0, sheets.EXHAUSTION_MAX, "Exhaustion")
    if problem:
        return {}, problem
    names = [n for n in names if n != "Exhaustion"]
    if level:  # a level brings the condition; level 0 means none
        names.append("Exhaustion")
    return {"conditions": names, "exhaustion": level or 0}, None


def _fill_defenses(s: Mapping[str, Any]) -> dict[str, str]:
    return {k: ", ".join(_list(s, k)) for k in ("resistances", "immunities", "vulnerabilities")}


def _apply_defenses(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    return {k: split_names(v[k]) for k in ("resistances", "immunities", "vulnerabilities")}, None


def _skill_key(name: str) -> str:
    return re.sub(r"[\s_]+", "-", name.strip().casefold())


def _skills_from(text: str) -> tuple[list[str], str | None]:
    out, bad = [], None
    for name in split_names(text):
        key = _skill_key(name)
        if key in sheets.SKILLS:
            out.append(key)
        elif bad is None:
            bad = name
    return out, bad


def _fill_skills(s: Mapping[str, Any]) -> dict[str, str]:
    return {
        "trained": ", ".join(skill_name(k) for k in _list(s, "skills")),
        "expert": ", ".join(skill_name(k) for k in _list(s, "expertise")),
    }


def _apply_skills(s: Mapping[str, Any], v: Mapping[str, str]) -> tuple[dict[str, Any], str | None]:
    trained, bad = _skills_from(v["trained"])
    if bad:
        return (
            {},
            f"'{bad}' isn't a skill DMbot knows. Use names like Perception or Sleight of Hand.",
        )
    expert, bad = _skills_from(v["expert"])
    if bad:
        return (
            {},
            f"'{bad}' isn't a skill DMbot knows. Use names like Perception or Sleight of Hand.",
        )
    if stray := [e for e in expert if e not in trained]:
        return {}, f"Expert in {skill_name(stray[0])}: add it to Trained skills too."
    return {"skills": sorted(set(trained)), "expertise": sorted(set(expert))}, None


def _fill_proficiencies(s: Mapping[str, Any]) -> dict[str, str]:
    return {k: ", ".join(_list(s, k)) for k in ("tools", "weapon_profs", "armor_profs")}


def _apply_proficiencies(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    return {k: split_names(v[k]) for k in ("tools", "weapon_profs", "armor_profs")}, None


def _fill_features(s: Mapping[str, Any]) -> dict[str, str]:
    return {"features": ", ".join(_list(s, "features")), "feats": ", ".join(_list(s, "feats"))}


def _apply_features(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    return {"features": split_names(v["features"]), "feats": split_names(v["feats"])}, None


def _fill_background(s: Mapping[str, Any]) -> dict[str, str]:
    return {"background": str(s.get("background") or "")}


def _apply_background(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    return {"background": v["background"].strip() or None}, None


def _fill_speed(s: Mapping[str, Any]) -> dict[str, str]:
    senses = s.get("senses") if isinstance(s.get("senses"), Mapping) else {}
    assert isinstance(senses, Mapping)
    out = {"speed": str(s["speed"]) if isinstance(s.get("speed"), int) else ""}
    out.update({sense: str(senses.get(sense, "")) for sense in sheets.SENSES})
    return out


def _apply_speed(s: Mapping[str, Any], v: Mapping[str, str]) -> tuple[dict[str, Any], str | None]:
    speed, problem = _int(v["speed"], 0, 200, "Speed")
    if problem:
        return {}, problem
    senses: dict[str, int] = {}
    for sense in sheets.SENSES:
        feet, problem = _int(v[sense], 1, 1000, sense.capitalize())
        if problem:
            return {}, problem
        if feet:
            senses[sense] = feet
    return {"speed": speed, "senses": senses}, None


def _fill_languages(s: Mapping[str, Any]) -> dict[str, str]:
    return {"languages": ", ".join(_list(s, "languages"))}


def _apply_languages(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    return {"languages": split_names(v["languages"])}, None


def _fill_inventory(s: Mapping[str, Any]) -> dict[str, str]:
    inv = s.get("inventory") if isinstance(s.get("inventory"), Mapping) else {}
    assert isinstance(inv, Mapping)
    rarity = inv.get("rarity") if isinstance(inv.get("rarity"), Mapping) else {}
    assert isinstance(rarity, Mapping)
    return {
        "items": ", ".join(_list(s, "items")),
        "weight": f"{inv['weight']:.2f}" if isinstance(inv.get("weight"), (int, float)) else "",
        "rarity": ", ".join(f"{r} {rarity[r]}" for r in sheets.RARITIES if rarity.get(r)),
    }


def _apply_inventory(
    s: Mapping[str, Any], v: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    rarity = dict.fromkeys(sheets.RARITIES, 0)
    for part in split_names(v["rarity"]):
        match = re.fullmatch(r"(very rare|[a-z]+)\s*[: ]\s*(\d{1,4})", part.strip().casefold())
        if match is None or match[1] not in rarity:
            return {}, (
                f"'{part}' isn't a rarity. Use {', '.join(sheets.RARITIES)}, like: "
                "common 14, uncommon 3."
            )
        if int(match[2]) > sheets.INVENTORY_MAX:
            return {}, f"'{part}': {match[1]} should be at most {sheets.INVENTORY_MAX}."
        rarity[match[1]] = int(match[2])
    weight_text = v["weight"].strip()
    try:
        weight = float(weight_text) if weight_text else 0.0
    except ValueError:
        return {}, "Weight: type just the number of pounds, like 45.5."
    if not 0 <= weight <= sheets.WEIGHT_MAX:
        return {}, f"Weight should be from 0 to {sheets.WEIGHT_MAX:g} pounds."
    count = sum(rarity.values())
    old = s.get("inventory") if isinstance(s.get("inventory"), Mapping) else {}
    assert isinstance(old, Mapping)
    if not count and s.get("origin") == sheets.ORIGIN_DOCUMENT:
        # A document seldom gives rarities, so its item count isn't their sum: leave it.
        kept = old.get("count")
        count = kept if isinstance(kept, int) else 0
    inventory = {"count": count, "weight": weight, "rarity": rarity} if (count or weight) else None
    return {"items": split_names(v["items"]), "inventory": inventory}, None


SECTIONS: tuple[Section, ...] = (
    Section(
        "abilities",
        "Abilities",
        _show_abilities,
        (Field("scores", "Scores in order: STR DEX CON INT WIS CHA", "10 14 12 8 10 13"),),
        _apply_abilities,
        _fill_abilities,
    ),
    Section(
        "conditions",
        "Conditions",
        _show_conditions,
        (
            Field("names", "Conditions", "Poisoned, Prone", names=True),
            Field("exhaustion", "Exhaustion level (0 to 6)", "0"),
        ),
        _apply_conditions,
        _fill_conditions,
    ),
    Section(
        "defenses",
        "Defenses",
        _show_defenses,
        (
            Field("resistances", "Resistant to", "Fire, Cold", names=True),
            Field("immunities", "Immune to", "Poison", names=True),
            Field("vulnerabilities", "Weak to", "Radiant", names=True),
        ),
        _apply_defenses,
        _fill_defenses,
    ),
    Section(
        "skills",
        "Skills",
        _show_skills,
        (
            Field("trained", "Trained skills", "Perception, Stealth", True, names=True),
            Field("expert", "Expert skills (must also be trained)", "Stealth", names=True),
        ),
        _apply_skills,
        _fill_skills,
    ),
    Section(
        "proficiencies",
        "Proficiencies",
        _show_proficiencies,
        (
            Field("tools", "Tools", "Thieves' tools", names=True),
            Field("weapon_profs", "Weapons", "Simple weapons", names=True),
            Field("armor_profs", "Armor", "Light armor, Shields", names=True),
        ),
        _apply_proficiencies,
        _fill_proficiencies,
    ),
    Section(
        "features",
        "Features and feats",
        _show_features,
        (
            Field("features", "Features", "Rage, Danger Sense", True, names=True),
            Field("feats", "Feats", "Alert", True, names=True),
        ),
        _apply_features,
        _fill_features,
        "Features",
    ),
    Section(
        "background",
        "Background",
        _show_background,
        (Field("background", "Background", "Outlander"),),
        _apply_background,
        _fill_background,
    ),
    Section(
        "speed",
        "Speed and senses",
        _show_speed,
        (
            Field("speed", "Speed (feet)", "30"),
            Field("darkvision", "Darkvision (feet, blank if none)"),
            Field("blindsight", "Blindsight (feet, blank if none)"),
            Field("tremorsense", "Tremorsense (feet, blank if none)"),
            Field("truesight", "Truesight (feet, blank if none)"),
        ),
        _apply_speed,
        _fill_speed,
        "Speed",
    ),
    Section(
        "languages",
        "Languages",
        _show_languages,
        (Field("languages", "Languages", "Common, Elvish", True, names=True),),
        _apply_languages,
        _fill_languages,
    ),
    Section(
        "inventory",
        "Inventory",
        _show_inventory,
        (
            Field("items", "Item names (optional)", "Rope, Longsword", True, names=True),
            Field("weight", "Total weight (pounds)", "45.5"),
            Field("rarity", "How many of each rarity", "common 14, uncommon 3, rare 1"),
        ),
        _apply_inventory,
        _fill_inventory,
    ),
)
BY_KEY = {section.key: section for section in SECTIONS}


# The snapshot keys each section owns, so "what changed" looks at the stored values, not at
# the shortened text a section shows (a name after the 25th is a change too).
OWNS: dict[str, tuple[str, ...]] = {
    "abilities": ("abilities",),
    "conditions": ("conditions", "exhaustion"),
    "defenses": ("resistances", "immunities", "vulnerabilities"),
    "skills": ("skills", "expertise"),
    "proficiencies": ("tools", "weapon_profs", "armor_profs"),
    "features": ("features", "feats"),
    "background": ("background",),
    "speed": ("speed", "senses"),
    "languages": ("languages",),
    "inventory": ("items", "inventory"),
}
# A part that was flagged as out of range is fine once its form has been saved.
FLAG_OF = {"abilities": "abilities", "inventory": "inventory"}


def edit(
    snapshot: Mapping[str, Any],
    section: Section,
    values: Mapping[str, str],
    *,
    kept: Mapping[str, list[str]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """(the new snapshot, a problem). A problem means nothing is to be saved. The result
    always went back through `sheets.clean`. `kept`: names a form box couldn't show (see
    `fit`), added back to that box's names."""
    if snapshot.get("source") == sheets.SOURCE_DNDBEYOND:
        return None, "Sheets from D&D Beyond are changed on D&D Beyond."
    values = dict(values)
    for field in section.fields:
        if not field.names:
            continue
        too_long = next((n for n in split_names(values[field.key]) if len(n) > sheets.NAME_MAX), "")
        if too_long:
            return None, f"'{too_long[:20]}…' is too long for a name."
        if kept and kept.get(field.key):
            values[field.key] = ", ".join([values[field.key], *kept[field.key]]).strip(", ")
    changes, problem = section.apply(snapshot, values)
    if problem:
        return None, problem
    merged = {**snapshot, **changes}
    flag = FLAG_OF.get(section.key)
    if flag and snapshot.get("flags"):
        merged["flags"] = [f for f in snapshot["flags"] if f != flag]
    cleaned = sheets.clean(merged)
    if cleaned is None:
        return None, "That didn't save. Check what you typed and try again."
    return cleaned, None


def changed_sections(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """The labels of the sections whose stored values differ, for "Changed: Skills"."""
    return [s.label for s in SECTIONS if any(old.get(k) != new.get(k) for k in OWNS[s.key])]
