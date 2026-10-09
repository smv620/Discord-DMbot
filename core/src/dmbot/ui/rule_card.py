"""The rule card the DM sees when they look something up (#908): what the free rules (the
SRD) say about a spell, a condition or a creature, word for word, with where it comes from.

Built here without Discord (only its text escaping), so it can be tested on its own, and
so the rules advisor's alerts can reuse it later. Nothing here decides anything: a house
rule that names the thing is shown first, the book comes after, and the DM decides what
applies. Nothing is guessed: a card is only made for a name the index knows; otherwise
`no_match_text` says so and offers close names to pick from.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import discord

from dmbot.rules import index
from dmbot.rules.house import HouseRule
from dmbot.rules.index import Entry, Hit, monster_keys, normalize

PART_MAX = 1900  # Discord allows 2,000 characters; the rest is for what never counts
CONTINUED_MAX = 120  # room for a later part's heading
HOUSE_LINE_MAX = 250  # one house rule, after escaping
HOUSE_SHOWN = 3  # most house rules shown; the rest are counted
FACTS_MAX = 400
NAME_MAX = 100
MIN_TEXT_ROOM = 200  # a first part with less room than this is only the heading

FREE_RULES = "the free rules (SRD)"
NOT_A_RULING = "_From the free rules (SRD). DMbot reads it out; you decide what applies._"
OLDER_NOTE = "_These are the older 2014 rules: the newer rules have nothing under this name._"
KIND_WORDS = {"spell": "spell", "condition": "condition", "monster": "creature"}

_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _fit(text: str, limit: int) -> str:
    """Shorten text to `limit` characters, never ending on half an escape."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if (len(cut) - len(cut.rstrip("\\"))) % 2:
        cut = cut[:-1]
    return cut + "…"


def level_words(entry: Entry) -> str:
    """ "3rd-level Evocation", "Evocation cantrip", "1st-level Divination (ritual)"."""
    level, school = int(entry.details["level"]), str(entry.details["school"])
    ritual = " (ritual)" if entry.details.get("ritual") else ""
    if level == 0:
        return f"{school} cantrip"
    return f"{_ORDINALS.get(level, f'{level}th')}-level {school}{ritual}"


def facts(entry: Entry) -> str:
    """The short line under the name: a spell's level, school, casting time, range,
    components and duration; a creature's size and type, AC, HP, speed and CR; nothing for
    a condition."""
    d = entry.details
    if entry.kind == "spell":
        parts = [
            level_words(entry),
            f"Casting time: {d['casting_time']}",
            f"Range: {d['range']}",
            f"Components: {d['components']}",
            f"Duration: {d['duration']}",
        ]
    elif entry.kind == "monster":
        parts = [
            f"{d['size']} {d['type']}",
            f"AC {d['ac']}",
            f"HP {d['hp']} ({d['hit_dice']})",
            f"Speed {d['speed']}",
            f"CR {d['cr']}",
        ]
    else:
        return ""
    return _fit(" · ".join(_md(str(p)) for p in parts), FACTS_MAX)


def house_matches(rules: Iterable[HouseRule], names: Iterable[str]) -> list[HouseRule]:
    """The house rules that name the thing: any of `names` (case, punctuation and
    apostrophes ignored) is a whole word or phrase in the rule or in what it replaces.
    From the rules given, which are one campaign's: this never looks further."""
    wanted = {n for n in (normalize(name) for name in names) if len(n) >= 2}
    found = []
    for rule in rules:
        text = f" {normalize(rule.rule)} {normalize(rule.supersedes or '')} "
        if any(f" {name} " in text for name in wanted):
            found.append(rule)
    return sorted(found, key=lambda r: r.number)


def house_lines(matches: Sequence[HouseRule]) -> list[str]:
    """ "🏠 House rule 12: ..." for each (the first few), and a count of the rest."""
    lines = []
    for rule in matches[:HOUSE_SHOWN]:
        words = _md(rule.rule)
        if rule.supersedes:
            words += f" (instead of: {_md(rule.supersedes)})"
        lines.append(_fit(f"🏠 **House rule {rule.number}:** {words}", HOUSE_LINE_MAX))
    extra = len(matches) - HOUSE_SHOWN
    if extra > 0:
        lines.append(
            f"🏠 …and {extra} more house rule{'s' if extra != 1 else ''}: `/dmbot houserules`"
        )
    return lines


def names_for(entry: Entry, typed: str) -> list[str]:
    """What a house rule might call this entry: its name, the name typed, and (a creature)
    its name without the bracket or turned round."""
    names = [entry.name, typed]
    if entry.kind == "monster":
        names += monster_keys(entry.name)
    return names


def split_text(text: str, first: int, rest: int) -> list[str]:
    """The text in parts that fit: the first part up to `first` characters, the others up
    to `rest`. Parts end between paragraphs when they can, else between sentences or
    words; no word is cut (unless one is longer than a whole part)."""
    atoms: list[str] = []
    limit = max(1, min(first, rest) if first >= MIN_TEXT_ROOM else rest)
    for line in text.split("\n"):
        atoms.extend(_atoms(line, limit))
    parts: list[str] = []
    current = ""
    for atom in atoms:
        budget = first if not parts else rest
        joined = f"{current}\n{atom}" if current else atom
        if len(joined) <= budget:
            current = joined
            continue
        if current:
            parts.append(current)
        current = atom
    if current or not parts:
        parts.append(current)
    return parts


def _atoms(line: str, limit: int) -> list[str]:
    """A line, or if it is longer than `limit` the pieces of it: sentences, then words."""
    if len(line) <= limit:
        return [line]
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", line):
        for word in _words(sentence, limit):
            joined = f"{current} {word}" if current else word
            if len(joined) <= limit:
                current = joined
            else:
                if current:
                    pieces.append(current)
                current = word
    if current:
        pieces.append(current)
    return pieces


def _words(sentence: str, limit: int) -> list[str]:
    """The sentence whole if it fits, else its words (a word longer than `limit` is cut)."""
    if len(sentence) <= limit:
        return [sentence]
    out = []
    for word in sentence.split(" "):
        while len(word) > limit:
            out.append(word[:limit])
            word = word[limit:]
        out.append(word)
    return out


def header(hit: Hit, typed: str, rules: Sequence[HouseRule]) -> str:
    """Everything above the text: house rules first, the name and facts, where it comes
    from, and any note about how it was found."""
    entry = hit.entry
    lines = house_lines(house_matches(rules, names_for(entry, typed)))
    kind = KIND_WORDS.get(entry.kind, entry.kind)
    lines.append(f"📖 **{_fit(_md(entry.name), NAME_MAX)}** ({kind})")
    line = facts(entry)
    if line:
        lines.append(line)
    lines.append(f"_Source: {_md(hit.citation)}_")
    asked = " ".join(typed.split())
    if hit.renamed and asked:
        lines.append(
            f"_{_fit(_md(asked), NAME_MAX)} is now called {_fit(_md(entry.name), NAME_MAX)}._"
        )
    elif normalize(asked) != normalize(entry.name) and asked:
        lines.append(
            f"_You typed {_fit(_md(asked), NAME_MAX)}; this is {_fit(_md(entry.name), NAME_MAX)}._"
        )
    if hit.tag:
        lines.append(OLDER_NOTE)
    lines.append(NOT_A_RULING)
    return "\n".join(lines)


def card_parts(hit: Hit, typed: str, rules: Sequence[HouseRule]) -> list[str]:
    """The card as messages, each short enough for Discord: the first has the heading and
    as much of the text as fits; the others carry on with the text."""
    head = header(hit, typed, rules)
    text = _md(hit.entry.text)
    room = PART_MAX - len(head) - 1
    rest = PART_MAX - CONTINUED_MAX
    if room < MIN_TEXT_ROOM:
        chunks = ["", *split_text(text, rest, rest)]
    else:
        chunks = split_text(text, room, rest)
    parts = [f"{head}\n{chunks[0]}" if chunks[0] else head]
    title = _fit(_md(hit.entry.name), NAME_MAX)
    total = len(chunks)
    for number, chunk in enumerate(chunks[1:], start=2):
        parts.append(f"📖 **{title}** (part {number} of {total})\n{chunk}")
    return parts


def no_match_text(typed: str, rules: Sequence[HouseRule], has_suggestions: bool) -> str:
    """When the index has no entry for the name: say so plainly, show any house rule that
    names it, and say what to do next. Nothing is guessed."""
    asked = _fit(_md(" ".join(typed.split())), NAME_MAX)
    lines = house_lines(house_matches(rules, [typed]))
    lines.append(f"DMbot couldn't find **{asked}** in {FREE_RULES}.")
    lines.append(
        "Only the free rules are in DMbot so far, not the books you own, so many things "
        "won't be here."
    )
    if has_suggestions:
        lines.append("**Did you mean…?** Press a name to read it.")
    else:
        lines.append("Check the spelling, or try a shorter name.")
    return "\n".join(lines)


def choice_label(entry: Entry) -> str:
    """How a name shows in the list that opens as the DM types: `Fireball (spell)`, and
    `Orc (creature, older 2014 rules)` for a name only the 2014 rules have."""
    kind = KIND_WORDS.get(entry.kind, entry.kind)
    older = ", older 2014 rules" if entry.edition == index.LEGACY else ""
    label = f"{entry.name} ({kind}{older})"
    return label if len(label) <= NAME_MAX else label[: NAME_MAX - 1] + "…"
