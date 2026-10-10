"""The house-rules file: a campaign's house rules as plain text a table can keep anywhere
(#969). Pure: no Discord, no database.

One rule per numbered line, with an optional "instead of":

    House rules: Frostmaiden
    # Keep the numbers: DMbot's alerts use them.
    12. Potions are a bonus action. (instead of: Drinking a potion takes an action.)

A title line (the first line that is not a rule) and lines starting with `#` are ignored.
`parse` and `write` go both ways without loss, except that a rule that itself contains
" (instead of: " is written with square brackets, since the file could not tell it apart
from the real one. A line that is not a rule is reported, never guessed at. `compare` says
what differs from DMbot's copy, by rule number.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.rules.house import HOUSE_RULES_MAX, NUMBER_MAX, RULE_MAX, HouseRule

MARK = " (instead of: "
LINE = re.compile(r"^\s*(?P<number>\d{1,10})\.\s+(?P<text>\S.*?)\s*$")
HELP = (
    "# One rule on each line: its number, a full stop, then the rule.\n"
    "# Keep the numbers as they are: DMbot's alerts use them."
)
NAME_MAX = 40

TOO_LONG = f"longer than {RULE_MAX} characters"
BAD_NUMBER = "the number is too big, or zero"
TWICE = "this number is used twice; the first one is kept"
NOT_A_RULE = "not a rule (a rule starts with its number and a full stop)"
EMPTY_RULE = "nothing after the number"
TOO_MANY = f"more than {HOUSE_RULES_MAX} rules; the rest are left out"


@dataclass(frozen=True, slots=True)
class FileRule:
    number: int
    rule: str
    supersedes: str | None = None


@dataclass(frozen=True, slots=True)
class Problem:
    line: int  # counted from 1
    text: str  # the line, cut short enough to show
    why: str


@dataclass(frozen=True, slots=True)
class Parsed:
    rules: tuple[FileRule, ...]
    problems: tuple[Problem, ...]


@dataclass(frozen=True, slots=True)
class Moved:
    """The same words under another number: not a new rule, not a removed one."""

    old: int
    new: int


@dataclass(frozen=True, slots=True)
class Diff:
    added: tuple[FileRule, ...] = ()  # in the file, not in DMbot
    changed: tuple[tuple[HouseRule, FileRule], ...] = ()  # (DMbot's, the file's)
    removed: tuple[HouseRule, ...] = ()  # in DMbot, not in the file
    moved: tuple[Moved, ...] = ()
    same: int = 0

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed or self.moved)

    @property
    def count(self) -> int:
        return len(self.added) + len(self.changed) + len(self.removed) + len(self.moved)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def write(campaign_name: str, rules: Sequence[HouseRule]) -> str:
    """The file for these rules (this campaign's), lowest number first."""
    lines = [f"House rules: {_one_line(campaign_name) or 'Campaign'}", HELP]
    for rule in sorted(rules, key=lambda r: r.number):
        text = _one_line(rule.rule).replace(MARK, " [instead of: ")
        line = f"{rule.number}. {text}"
        if rule.supersedes:
            line += f"{MARK}{_one_line(rule.supersedes)})"
        lines.append(line)
    return "\n".join(lines) + "\n"


def parse(text: str) -> Parsed:
    """The rules in a file, and the lines that could not be read. Never raises."""
    rules: dict[int, FileRule] = {}
    problems: list[Problem] = []
    titled = False
    for n, raw in enumerate(text.lstrip("﻿").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.match(line)
        if match is None:
            if not titled and not rules and not problems:
                titled = True  # the first line that is not a rule is the title
            else:
                problems.append(Problem(n, _shown(line), NOT_A_RULE))
            continue
        titled = True
        number, body = int(match["number"]), match["text"]
        if not 1 <= number <= NUMBER_MAX:
            problems.append(Problem(n, _shown(line), BAD_NUMBER))
            continue
        rule, instead = _split(body)
        if not rule:
            problems.append(Problem(n, _shown(line), EMPTY_RULE))
        elif len(rule) > RULE_MAX or (instead is not None and len(instead) > RULE_MAX):
            problems.append(Problem(n, _shown(line), TOO_LONG))
        elif number in rules:
            problems.append(Problem(n, _shown(line), TWICE))
        elif len(rules) >= HOUSE_RULES_MAX:
            problems.append(Problem(n, _shown(line), TOO_MANY))
        else:
            rules[number] = FileRule(number, _one_line(rule), instead)
    return Parsed(tuple(rules[k] for k in sorted(rules)), tuple(problems))


def _split(body: str) -> tuple[str, str | None]:
    """(the rule, the "instead of" or None): the last " (instead of: …)" that ends the line."""
    if body.endswith(")") and (at := body.rfind(MARK)) >= 0:
        instead = _one_line(body[at + len(MARK) : -1])
        return _one_line(body[:at]), instead or None
    return _one_line(body), None


def _shown(line: str) -> str:
    return line if len(line) <= 80 else line[:79] + "…"


def compare(mine: Sequence[HouseRule], theirs: Sequence[FileRule]) -> Diff:
    """What differs between DMbot's rules and a file's, by number. The same words under
    another number are `moved`, not an add and a remove."""
    have = {r.number: r for r in mine}
    file = {r.number: r for r in theirs}
    added = [r for n, r in sorted(file.items()) if n not in have]
    removed = [r for n, r in sorted(have.items()) if n not in file]
    changed = [(have[n], r) for n, r in sorted(file.items()) if n in have and _differs(have[n], r)]
    same = sum(1 for n, r in file.items() if n in have and not _differs(have[n], r))
    moved: list[Moved] = []
    for new in list(added):
        old = next((r for r in removed if _alike(r, new)), None)
        if old is not None:
            moved.append(Moved(old.number, new.number))
            added.remove(new)
            removed.remove(old)
    return Diff(tuple(added), tuple(changed), tuple(removed), tuple(moved), same)


def _differs(mine: HouseRule, theirs: FileRule) -> bool:
    return not _alike(mine, theirs)


def _alike(mine: HouseRule, theirs: FileRule) -> bool:
    return (
        _one_line(mine.rule) == theirs.rule
        and (_one_line(mine.supersedes or "") or None) == theirs.supersedes
    )


def filename(campaign_name: str) -> str:
    """`house-rules-frostmaiden.txt`: plain letters and digits only."""
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower().encode("ascii", "ignore").decode())
    slug = slug.strip("-")[:NAME_MAX].strip("-")
    return f"house-rules-{slug or 'campaign'}.txt"
