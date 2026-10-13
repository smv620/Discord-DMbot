"""The import status for a character sheet (#1124; docs/PLAN.md, "Character sheets, part D").

After a sheet is linked or typed in, the player gets one private card with one line per part
of the sheet:

- ✅ read and checked;
- ⚠️ read, but a name isn't in DMbot's rules list (content outside the free rules, or
  homebrew). It may be perfectly fine, and the card says so plainly;
- ⛔ couldn't be read, or doesn't add up (levels over 20, hit points over max plus temporary,
  a number below zero, a name that is nothing like any DMbot knows).

The checks are the rules index (`dmbot.rules.index`, spells and conditions of the SRD),
`sheet_names` (classes, species and feats) and simple arithmetic. No AI, and nothing is
guessed: a name is only ever ✅ if it is in a list. Names from D&D Beyond are its own data, so a
name DMbot doesn't know is ⚠️ there; a name someone typed or a document gave is ⛔ when it is
nothing like a known name (it may be a typo), and ⚠️ when it nearly is one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dmbot.memory import sheet_names
from dmbot.memory.sheets import ABILITIES, RARITIES, SOURCE_DNDBEYOND, SOURCE_TYPED
from dmbot.rules.index import srd

OK, WARN, BAD = "ok", "warn", "bad"
EMOJI = {OK: "✅", WARN: "⚠️", BAD: "⛔"}
_WORST = {OK: 0, WARN: 1, BAD: 2}
SHOWN_NAMES = 3  # names listed before "and N more": the card stays under one message
CARD_MAX = 1800  # Discord allows 2000 characters in a message
MAX_LEVEL = 20


@dataclass(frozen=True, slots=True)
class Check:
    part: str
    status: str  # OK, WARN or BAD
    text: str

    @property
    def line(self) -> str:
        return f"{EMOJI[self.status]} {self.text}"


def _few(names: list[str], shown: int = SHOWN_NAMES) -> str:
    """ "A, B, C and 4 more": long lists never run the card past one message."""
    head = ", ".join(names[:shown])
    return f"{head} and {len(names) - shown} more" if len(names) > shown else head


def _quoted(names: list[str]) -> str:
    return _few([f"'{n}'" for n in names], 2)


def _worse(a: str, b: str) -> str:
    return a if _WORST[a] >= _WORST[b] else b


def _classes(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = snapshot.get("classes")
    return [c for c in value if isinstance(c, Mapping)] if isinstance(value, list) else []


def _list(snapshot: Mapping[str, Any], key: str) -> list[str]:
    value = snapshot.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def vitals(snapshot: Mapping[str, Any]) -> str:
    """ "HP 11 / 26 + 0, AC 18": current / maximum + temporary. What is not known is left
    out."""
    parts = []
    hp, top = snapshot.get("hp"), snapshot.get("max_hp")
    if isinstance(top, int):
        now = f"{hp} / " if isinstance(hp, int) else ""
        parts.append(f"HP {now}{top} + {snapshot.get('temp_hp') or 0}")
    if isinstance(snapshot.get("ac"), int):
        parts.append(f"AC {snapshot['ac']}")
    return ", ".join(parts)


def class_line(snapshot: Mapping[str, Any]) -> str:
    """ "Ranger 3 + Druid 3"."""
    return " + ".join(f"{c.get('name')} {c.get('level')}" for c in _classes(snapshot))


def _kind_of(snapshot: Mapping[str, Any], kind: str, name: str) -> tuple[str, str | None]:
    """(status, a note) for a class, species or feat name."""
    found = sheet_names.where(kind, name)
    if found == "srd":
        return OK, None
    if found == "official":
        return WARN, None
    near = sheet_names.close(kind, name)
    if snapshot.get("source") == SOURCE_DNDBEYOND or near is not None:
        return WARN, near
    return BAD, None


def _spell_status(snapshot: Mapping[str, Any], name: str, target: str, fallback: str) -> str:
    index = srd()
    if index.lookup(name, target, fallback, kind="spell") is not None:
        return OK
    if snapshot.get("source") == SOURCE_DNDBEYOND or index.suggest(name, target, fallback, limit=1):
        return WARN
    return BAD


def _named(
    part: str,
    label: str,
    names: list[str],
    status_of: Callable[[str], str],
) -> Check:
    """A list of names: ⛔ names first (they say what is wrong), then ⚠️, then how many were
    checked. A list that is empty is ✅ with nothing to check."""
    bad = [n for n in names if status_of(n) == BAD]
    warn = [n for n in names if status_of(n) == WARN]
    rest = len(names) - len(bad) - len(warn)
    if bad:
        others = rest + len(warn)
        return Check(
            part,
            BAD,
            f"{label}: {_quoted(bad)} not recognized"
            + (f"; {others} others checked" if others else ""),
        )
    if warn:
        return Check(
            part,
            WARN,
            f"{label}: {_quoted(warn)} {'isn' if len(warn) == 1 else 'aren'}'t in DMbot's free "
            "rules list. That is fine if it is from a book or your own"
            + (f"; {rest} others checked" if rest else ""),
        )
    if not names:
        return Check(part, OK, f"{label}: none")
    return Check(part, OK, f"{label} ({len(names)}): {_few(names)}")


def checks(
    snapshot: Mapping[str, Any], *, target: str = "2024", fallback: str = "2014"
) -> list[Check]:
    """One check per part of the sheet, in the order the card shows them. A sheet someone
    typed in has only the parts the form asks for, so only those are checked."""
    out = [_basic(snapshot)]
    if snapshot.get("source") == SOURCE_TYPED:
        out.append(_features(snapshot))
        return out
    out += [
        _abilities(snapshot),
        _conditions(snapshot),
        _defenses(snapshot),
        _skills(snapshot),
        _proficiencies(snapshot),
        _features(snapshot),
        _named(
            "feats",
            "Feats",
            _list(snapshot, "feats"),
            lambda n: _kind_of(snapshot, "feat", n)[0],
        ),
        _more(snapshot),
        _named(
            "spells",
            "Spells",
            _list(snapshot, "spells"),
            lambda n: _spell_status(snapshot, n, target, fallback),
        ),
        _inventory(snapshot),
    ]
    return out


def _basic(snapshot: Mapping[str, Any]) -> Check:
    status, problems = OK, []
    classes = _classes(snapshot)
    total = sum(c["level"] for c in classes if isinstance(c.get("level"), int))
    if not classes:
        status = BAD
        problems.append("no class or level could be read")
    elif total > MAX_LEVEL:
        status = BAD
        problems.append(f"the levels add up to {total} and the most is {MAX_LEVEL}")
    top, hp, temp = snapshot.get("max_hp"), snapshot.get("hp"), snapshot.get("temp_hp") or 0
    typed = snapshot.get("source") == SOURCE_TYPED  # the form never asks for hit points or AC
    if not isinstance(top, int):
        if not typed:
            status = BAD
            problems.append("hit points couldn't be read")
    elif isinstance(hp, int) and hp > top + temp:
        status = BAD
        problems.append(f"HP {hp} is more than the maximum {top} + {temp}")
    if not typed and not isinstance(snapshot.get("ac"), int):
        status = BAD
        problems.append("armor class couldn't be worked out")
    if "basic" in (snapshot.get("flags") or []):
        status = BAD
        problems.append("a number on the sheet is below zero")
    odd: list[str] = []
    for c in classes:
        one, _near = _kind_of(snapshot, "class", str(c.get("name")))
        if one != OK:
            odd.append(f"'{c.get('name')}'")
            status = _worse(status, one)
    species = snapshot.get("species")
    if isinstance(species, str):
        one, _near = _kind_of(snapshot, "species", species)
        if one != OK:
            odd.append(f"'{species}'")
            status = _worse(status, one)
    if odd and status != BAD:
        problems.append(
            f"{_few(odd, 2)} {'isn' if len(odd) == 1 else 'aren'}'t in DMbot's free rules "
            "list. That is fine if it is from a book or your own"
        )
    elif odd:
        problems.append(f"{_few(odd, 2)} not recognized")
    summary = ", ".join(
        p for p in (snapshot.get("name"), class_line(snapshot), vitals(snapshot)) if p
    )
    text = f"Basic: {summary}" + (f" ({'; '.join(problems)})" if problems else "")
    return Check("basic", status, text)


def _abilities(snapshot: Mapping[str, Any]) -> Check:
    scores = snapshot.get("abilities") if isinstance(snapshot.get("abilities"), Mapping) else {}
    assert isinstance(scores, Mapping)
    missing = [a.upper() for a in ABILITIES if a not in scores]
    if "abilities" in (snapshot.get("flags") or []):
        return Check("abilities", BAD, "Ability scores: a score on the sheet is below zero")
    if missing:
        what = "couldn't be read" if len(missing) == 6 else f"{_few(missing, 6)} couldn't be read"
        return Check("abilities", BAD, f"Ability scores: {what}")
    shown = " ".join(f"{a.upper()} {scores[a]}" for a in ABILITIES)
    return Check("abilities", OK, f"Ability scores: {shown}")


def _conditions(snapshot: Mapping[str, Any]) -> Check:
    names = _list(snapshot, "conditions")
    exhaustion = snapshot.get("exhaustion") or 0
    shown = [f"Exhaustion {exhaustion}" if n == "Exhaustion" and exhaustion else n for n in names]
    return Check("conditions", OK, f"Conditions: {_few(shown, 6) if shown else 'none right now'}")


def _defenses(snapshot: Mapping[str, Any]) -> Check:
    bits = [
        f"{label} {_few(_list(snapshot, key), 2)}"
        for label, key in (
            ("resists", "resistances"),
            ("immune to", "immunities"),
            ("vulnerable to", "vulnerabilities"),
        )
        if _list(snapshot, key)
    ]
    return Check("defenses", OK, f"Defenses: {'; '.join(bits) if bits else 'none'}")


def _skills(snapshot: Mapping[str, Any]) -> Check:
    skills, expert = _list(snapshot, "skills"), _list(snapshot, "expertise")
    stray = [e for e in expert if e not in skills]
    if stray:
        return Check(
            "skills",
            BAD,
            f"Skills: expertise in {_few(stray, 2)} without being proficient, which doesn't add up",
        )
    return Check("skills", OK, f"Skills: {len(skills)} proficient, {len(expert)} with expertise")


def _proficiencies(snapshot: Mapping[str, Any]) -> Check:
    parts = [
        f"{len(_list(snapshot, key))} {label}"
        for label, key in (
            ("tools", "tools"),
            ("weapons", "weapon_profs"),
            ("armor", "armor_profs"),
        )
    ]
    return Check("proficiencies", OK, f"Proficiencies: {', '.join(parts)}")


def _features(snapshot: Mapping[str, Any]) -> Check:
    names = _list(snapshot, "features")
    return Check(
        "features", OK, f"Features ({len(names)}): {_few(names)}" if names else "Features: none"
    )


def _more(snapshot: Mapping[str, Any]) -> Check:
    senses = snapshot.get("senses") if isinstance(snapshot.get("senses"), Mapping) else {}
    assert isinstance(senses, Mapping)
    bits = [
        f"background {snapshot['background']}" if snapshot.get("background") else "",
        f"speed {snapshot['speed']} ft" if isinstance(snapshot.get("speed"), int) else "",
        f"{len(senses)} {'sense' if len(senses) == 1 else 'senses'}" if senses else "",
        f"{len(_list(snapshot, 'languages'))} languages",
    ]
    return Check(
        "more", OK, f"Background, speed, senses and languages: {', '.join(b for b in bits if b)}"
    )


def _inventory(snapshot: Mapping[str, Any]) -> Check:
    inv = snapshot.get("inventory")
    if not isinstance(inv, Mapping):
        return Check("inventory", BAD, "Inventory: couldn't be read")
    if "inventory" in (snapshot.get("flags") or []):
        return Check("inventory", BAD, "Inventory: an amount on the sheet is below zero")
    rarity = inv.get("rarity") if isinstance(inv.get("rarity"), Mapping) else {}
    assert isinstance(rarity, Mapping)
    count = inv.get("count") or 0
    if sum(rarity.get(r, 0) for r in RARITIES) != count:
        return Check("inventory", BAD, "Inventory: the rarity counts don't add up to the items")
    counts = [f"{rarity[r]} {r}" for r in reversed(RARITIES) if rarity.get(r)]
    weight = f"{inv.get('weight') or 0:.2f}"
    tail = f": {', '.join(counts)}" if counts else ""
    return Check("inventory", OK, f"Inventory ({count} items, {weight} lb){tail}")


# ---- the card ---------------------------------------------------------------------------


def card(snapshot: Mapping[str, Any], *, target: str = "2024", fallback: str = "2014") -> str:
    """The import status card text: one line per part, under one message."""
    done = checks(snapshot, target=target, fallback=fallback)
    lines = [c.line for c in done]
    bad = sum(c.status == BAD for c in done)
    warn = sum(c.status == WARN for c in done)
    if bad:
        foot = (
            "⛔ means DMbot couldn't read it or it doesn't add up. Check that part on your sheet."
        )
    elif warn:
        foot = (
            "⚠️ means DMbot doesn't know a name. It may be from a book or your own, which is fine."
        )
    else:
        foot = "Everything DMbot read looks right."
    text = "\n".join(
        [
            f"📜 **{snapshot.get('name')}**: here is what DMbot read. Only you see this.",
            *lines,
            foot,
        ]
    )
    return text if len(text) <= CARD_MAX else text[: CARD_MAX - 1] + "…"


def changed(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> bool:
    """Whether a re-read found anything different (a card is sent after a re-read only if so)."""
    return old is None or dict(old) != dict(new)
