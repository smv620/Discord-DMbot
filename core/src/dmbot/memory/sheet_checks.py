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
from dmbot.memory.sheets import (
    ABILITIES,
    INVENTORY_MAX,
    ORIGIN_DOCUMENT,
    RARITIES,
    SOURCE_DNDBEYOND,
    SOURCE_TYPED,
)
from dmbot.rules.index import srd

OK, WARN, BAD = "ok", "warn", "bad"
EMOJI = {OK: "✅", WARN: "⚠️", BAD: "⛔"}
_WORST = {OK: 0, WARN: 1, BAD: 2}
SHOWN_NAMES = 3  # names listed before "and N more": the card stays under one message
CARD_MAX = 1900  # Discord allows 2000 characters in a message
LINE_MAX = 140  # one line of the card; a longer one is cut, never the footer
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


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"


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
    if isinstance(top, int) and isinstance(hp, int):
        parts.append(f"HP {hp} / {top} + {snapshot.get('temp_hp') or 0}")
    elif isinstance(top, int):  # damage taken isn't known: say it is the maximum
        parts.append(f"max HP {top}")
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
    near_of: Callable[[str], str | None] | None = None,
) -> Check:
    """A list of names: ⛔ names first (they say what is wrong), then ⚠️, then how many were
    checked. A list that is empty is ✅ with nothing to check."""
    statuses = {n: status_of(n) for n in names}  # once each: a spell lookup isn't free
    bad = [n for n in names if statuses[n] == BAD]
    warn = [n for n in names if statuses[n] == WARN]
    rest = len(names) - len(bad) - len(warn)
    if bad:
        others = rest + len(warn)
        return Check(
            part,
            BAD,
            f"{label}: {_quoted(bad)} not recognized"
            + (f"; {others} others checked" if others else "")
            + ". Check the spelling on your sheet.",
        )
    if warn:
        return Check(
            part,
            WARN,
            f"{label}: {_quoted(warn)} {'isn' if len(warn) == 1 else 'aren'}'t on DMbot's list"
            + _did_you_mean(warn, near_of)
            + (f" ({rest} others fine)" if rest else ""),
        )
    if not names:
        return Check(part, OK, f"{label}: none")
    return Check(part, OK, f"{label} ({len(names)}): {_few(names)}")


def _did_you_mean(names: list[str], near_of: Callable[[str], str | None] | None) -> str:
    """ "(did you mean Sharpshooter?)" for the first name that is nearly a known one. Only
    said, never changed."""
    if near_of is None:
        return ""
    for name in names:
        near = near_of(name)
        if near is not None and near.casefold() != name.casefold():
            return f" (did you mean {near}?)"
    return ""


def checks(
    snapshot: Mapping[str, Any], *, target: str = "2024", fallback: str = "2014"
) -> list[Check]:
    """One check per part of the sheet, in the order the card shows them. A sheet someone
    typed in has only the parts the form asks for, so only those are checked."""
    out = [_basic(snapshot)]
    if snapshot.get("source") == SOURCE_TYPED and snapshot.get("origin") != ORIGIN_DOCUMENT:
        out.append(_typed_names(snapshot, target, fallback))
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
            lambda n: sheet_names.close("feat", n),
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
        problems.append("no class or level could be read; link the sheet again")
    elif total > MAX_LEVEL:
        status = BAD
        problems.append(
            f"your class levels add up to {total}; the most is {MAX_LEVEL}. Check your levels"
        )
    top, hp, temp = snapshot.get("max_hp"), snapshot.get("hp"), snapshot.get("temp_hp") or 0
    typed = snapshot.get("source") == SOURCE_TYPED  # the form never asks for hit points or AC
    if not isinstance(top, int):
        if not typed:
            status = BAD
            problems.append("hit points couldn't be read; link the sheet again")
    elif isinstance(hp, int) and hp > top + temp:
        status = BAD
        problems.append(f"HP {hp} is above the maximum {top} + {temp}. Check HP on your sheet")
    if not typed and not isinstance(snapshot.get("ac"), int):
        status = BAD
        problems.append("armor class couldn't be worked out; check it on your sheet")
    if "basic" in (snapshot.get("flags") or []):
        status = BAD
        problems.append("a number on the sheet is below zero. Check your sheet")
    unknown: list[str] = []  # nothing like a name DMbot knows (⛔)
    unlisted: list[str] = []  # a real book's name, or D&D Beyond's own: DMbot just lacks it (⚠️)
    names = [(f"'{c.get('name')}'", "class", str(c.get("name"))) for c in classes]
    if isinstance(snapshot.get("species"), str):
        names.append((f"'{snapshot['species']}'", "species", snapshot["species"]))
    for shown, kind, name in names:
        one, _near = _kind_of(snapshot, kind, name)
        if one == BAD:
            unknown.append(shown)
        elif one == WARN:
            unlisted.append(shown)
    if unknown:
        status = BAD
        problems.append(f"{_few(unknown, 2)} not recognized. Check the spelling")
    if unlisted:
        status = _worse(status, WARN)
        problems.append(
            f"{_few(unlisted, 2)} {'isn' if len(unlisted) == 1 else 'aren'}'t on DMbot's list"
        )
    summary = ", ".join(
        p for p in (snapshot.get("name"), class_line(snapshot), vitals(snapshot)) if p
    )
    # The reason comes first, so it is always what is read (a long line is cut at its end);
    # the character follows in brackets.
    text = f"Character: {'; '.join(problems)}. ({summary})" if problems else f"Character: {summary}"
    return Check("basic", status, text)


def _abilities(snapshot: Mapping[str, Any]) -> Check:
    scores = snapshot.get("abilities") if isinstance(snapshot.get("abilities"), Mapping) else {}
    assert isinstance(scores, Mapping)
    missing = [a.upper() for a in ABILITIES if a not in scores]
    if "abilities" in (snapshot.get("flags") or []):
        return Check(
            "abilities", BAD, "Ability scores: a score on the sheet is below zero. Check your sheet"
        )
    if missing and snapshot.get("origin") == ORIGIN_DOCUMENT:  # the AI leaves out what isn't there
        if len(missing) == 6:
            return Check("abilities", WARN, "Ability scores: none found in your document")
        return Check(
            "abilities", WARN, f"Ability scores: {_few(missing, 6)} not found in your document"
        )
    if missing:
        what = "couldn't be read" if len(missing) == 6 else f"{_few(missing, 6)} couldn't be read"
        return Check("abilities", BAD, f"Ability scores: {what}. Link the sheet again")
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
            ("immune:", "immunities"),
            ("weak to", "vulnerabilities"),
        )
        if _list(snapshot, key)
    ]
    return Check("defenses", OK, f"Resistances: {'; '.join(bits) if bits else 'none'}")


def _skills(snapshot: Mapping[str, Any]) -> Check:
    skills, expert = _list(snapshot, "skills"), _list(snapshot, "expertise")
    stray = [e for e in expert if e not in skills]
    if stray:
        return Check(
            "skills",
            BAD,
            f"Skills: expert in {_few(stray, 2)}, but not trained in it. Check your sheet",
        )
    return Check("skills", OK, f"Skills: {len(skills)} trained, {len(expert)} expert")


def _proficiencies(snapshot: Mapping[str, Any]) -> Check:
    parts = [
        _plural(len(_list(snapshot, key)), word, many)
        for word, many, key in (
            ("tool", "tools", "tools"),
            ("weapon type", "weapon types", "weapon_profs"),
            ("armor type", "armor types", "armor_profs"),
        )
    ]
    return Check("proficiencies", OK, f"Trained with: {', '.join(parts)}")


def _typed_names(snapshot: Mapping[str, Any], target: str, fallback: str) -> Check:
    """The spell, feature and item names a player typed in (#1124): ✅ if one is a spell in
    the rules index or an SRD feat, else ⚠️ (it may be a class feature, an item, or from a
    book), with "did you mean" when it is nearly a spell or feat. Never ⛔: the form mixes
    kinds of names, so a name that is none of the two lists is not an error."""
    index = srd()

    def status(name: str) -> str:
        if index.lookup(name, target, fallback, kind="spell") is not None:
            return OK
        return OK if sheet_names.where("feat", name) == "srd" else WARN

    def near(name: str) -> str | None:
        found = index.suggest(name, target, fallback, limit=1)
        return found[0].name if found else sheet_names.close("feat", name)

    return _named("features", "Names you typed", _list(snapshot, "features"), status, near)


def _features(snapshot: Mapping[str, Any]) -> Check:
    names = _list(snapshot, "features")
    return Check(
        "features", OK, f"Features ({len(names)}): {_few(names)}" if names else "Features: none"
    )


def _more(snapshot: Mapping[str, Any]) -> Check:
    senses = snapshot.get("senses") if isinstance(snapshot.get("senses"), Mapping) else {}
    assert isinstance(senses, Mapping)
    bits = [
        str(snapshot["background"]) if snapshot.get("background") else "",
        f"{snapshot['speed']} ft speed" if isinstance(snapshot.get("speed"), int) else "",
        _plural(len(senses), "sense") if senses else "",
        _plural(len(_list(snapshot, "languages")), "language")
        if _list(snapshot, "languages")
        else "no languages found",
    ]
    return Check("more", OK, f"Also read: {', '.join(b for b in bits if b)}")


def _inventory(snapshot: Mapping[str, Any]) -> Check:
    inv = snapshot.get("inventory")
    if not isinstance(inv, Mapping) and snapshot.get("origin") == ORIGIN_DOCUMENT:
        return Check("inventory", OK, "Inventory: not found in your document")
    if not isinstance(inv, Mapping):
        return Check("inventory", BAD, "Inventory: couldn't be read. Link the sheet again")
    if "inventory" in (snapshot.get("flags") or []):
        return Check(
            "inventory", BAD, "Inventory: an amount on the sheet is below zero. Check your sheet"
        )
    rarity = inv.get("rarity") if isinstance(inv.get("rarity"), Mapping) else {}
    assert isinstance(rarity, Mapping)
    count = inv.get("count") or 0
    capped = count >= INVENTORY_MAX  # a count cut at the limit can't be matched to its parts
    if not capped and sum(rarity.get(r, 0) for r in RARITIES) != count:
        return Check(
            "inventory",
            BAD,
            "Inventory: DMbot couldn't count your items properly. Link the sheet again",
        )
    counts = [f"{rarity[r]} {r}" for r in reversed(RARITIES) if rarity.get(r)]
    weight = f"{inv.get('weight') or 0:.2f}"
    tail = f": {', '.join(counts)}" if counts else ""
    return Check("inventory", OK, f"Inventory ({_plural(count, 'item')}, {weight} lb){tail}")


# ---- the card ---------------------------------------------------------------------------


def card(
    snapshot: Mapping[str, Any],
    *,
    target: str = "2024",
    fallback: str = "2014",
    escape: Callable[[str], str] = str,
) -> str:
    """The import status card text: a summary first, then one line per part with the ones to
    look at at the top, then what the marks mean. `escape` makes a line safe for Discord's
    markdown (names are other people's words): the caller gives it, so this module doesn't
    need Discord. Under one message: a ✅ line is shortened or folded first; a ⚠️ or ⛔ line
    is never cut, so the reason is always there, and neither is the footer."""
    done = checks(snapshot, target=target, fallback=fallback)
    done.sort(key=lambda c: -_WORST[c.status])  # stable: ⛔ first, then ⚠️, then ✅
    bad = sum(c.status == BAD for c in done)
    warn = sum(c.status == WARN for c in done)
    if bad:
        summary = f"{_plural(bad, 'thing')} to check."
    elif warn:
        summary = f"{_plural(warn, 'name')} to glance at."
    else:
        summary = "Nothing to fix that DMbot can see."
    notes = []
    if warn:
        notes.append(
            "⚠️ is not a problem. DMbot only knows the free rules, so book and homebrew names "
            "show up here."
        )
    if bad:
        notes.append(
            "⛔ means DMbot couldn't read that part, or the numbers look off. Please look at it "
            "on your sheet. DMbot has changed nothing."
        )
    if not (bad or warn):
        notes.append("DMbot only checks names and numbers. You know your character best.")
    name = escape(str(snapshot.get("name")))
    head = f"📜 **{name}**: DMbot read your sheet. {summary} Only you see this."
    lines = []
    for c in done:
        text = escape(c.text)
        if c.status == OK and len(text) > LINE_MAX:
            text = text[: LINE_MAX - 1] + "…"
        lines.append(f"{EMOJI[c.status]} {text}")
    fine = [i for i, c in enumerate(done) if c.status == OK]
    out = "\n".join([head, *lines, *notes])
    while len(out) > CARD_MAX and fine:  # too long: fold the ✅ lines away, last first
        lines[fine.pop()] = ""
        shown = [ln for ln in lines if ln]
        more = len(lines) - len(shown)
        out = "\n".join([head, *shown, f"✅ and {more} more parts fine", *notes])
    return out


def changed(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> bool:
    """Whether a re-read found anything different (a card is sent after a re-read only if so)."""
    return old is None or dict(old) != dict(new)
