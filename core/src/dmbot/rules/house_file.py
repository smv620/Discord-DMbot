"""The house-rules file: a campaign's house rules as plain text a table can keep anywhere
(#969). Pure: no Discord, no database.

One rule per numbered line, with an optional "instead of":

    House rules: Frostmaiden
    # Keep the numbers: DMbot's alerts use them.
    12. Potions are a bonus action. (instead of: Drinking a potion takes an action.)

A title line (the first line, if it does not start with a number) and lines starting with
`#` are ignored.
`parse` and `write` go both ways without loss, except that words that themselves contain
" (instead of: " (in a rule or in its "instead of") are written with square brackets, since
the file could not tell them apart from the real one. A line that is not a rule is reported,
never guessed at. `compare` says what differs from DMbot's copy, by rule number.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.rules.house import HOUSE_RULES_MAX, NUMBER_MAX, RULE_MAX, HouseRule

MARK = " (instead of: "
BRACKETED = " [instead of: "
MAX_FILE_CHARS = 200_000  # 200 rules of 500 + 500 characters is 200,000 at the very most
MAX_LINE = 2_000
MAX_PROBLEMS = 20
HELP = (
    "# One rule per line: number, a dot, then the rule.\n"
    "# (instead of: ...) names the book rule your rule replaces.\n"
    "# Keep the numbers as they are: DMbot's alerts use them."
)
NAME_MAX = 40

TOO_LONG = f"longer than {RULE_MAX} characters (or the line is over {MAX_LINE})"
TOO_BIG = f"the file is over {MAX_FILE_CHARS:,} characters, so none of it was read"
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
        text = _one_line(rule.rule).replace(MARK, BRACKETED)
        line = f"{rule.number}. {text}"
        if rule.supersedes:
            line += f"{MARK}{_one_line(rule.supersedes).replace(MARK, BRACKETED)})"
        lines.append(line)
    return "\n".join(lines) + "\n"


def parse(text: str) -> Parsed:
    """The rules in a file, and the lines that could not be read. Never raises, and costs
    time in proportion to the file: it is cut off at `MAX_FILE_CHARS`, a line over
    `MAX_LINE` is refused unread, and at most `MAX_PROBLEMS` are listed (then a count)."""
    if len(text) > MAX_FILE_CHARS:
        return Parsed((), (Problem(0, "", TOO_BIG),))
    rules: dict[int, FileRule] = {}
    problems: list[Problem] = []
    unlisted = 0
    seen_any = False

    def report(n: int, line: str, why: str) -> None:
        nonlocal unlisted
        if len(problems) < MAX_PROBLEMS:
            problems.append(Problem(n, _shown(line), why))
        else:
            unlisted += 1

    for n, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        if len(raw) > MAX_LINE:
            report(n, raw, TOO_LONG)
            seen_any = True
            continue
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, dot, body = line.partition(".")
        is_rule = bool(dot) and head.isascii() and head.isdigit() and len(head) <= 10
        is_rule = is_rule and body[:1].isspace() and bool(body.strip())
        if not is_rule:
            if not seen_any and not line[0].isdigit():
                seen_any = True  # the first line, if it doesn't begin like a rule, is the title
            else:  # (a damaged rule such as "1) Potions" is reported, never taken for a title)
                report(n, line, NOT_A_RULE)
                seen_any = True
            continue
        seen_any = True
        number = int(head)
        if not 1 <= number <= NUMBER_MAX:
            report(n, line, BAD_NUMBER)
            continue
        rule, instead = _split(body.strip())
        if not rule:
            report(n, line, EMPTY_RULE)
        elif len(rule) > RULE_MAX or (instead is not None and len(instead) > RULE_MAX):
            report(n, line, TOO_LONG)
        elif number in rules:
            report(n, line, TWICE)
        elif len(rules) >= HOUSE_RULES_MAX:
            report(n, line, TOO_MANY)
            break  # the rest is not read
        else:
            rules[number] = FileRule(number, _one_line(rule), instead)
    if unlisted:
        problems.append(Problem(0, "", f"…and {unlisted} more lines could not be used"))
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
    changed = [
        (have[n], r) for n, r in sorted(file.items()) if n in have and not _alike(have[n], r)
    ]
    same = sum(1 for n, r in file.items() if n in have and _alike(have[n], r))
    by_words: dict[tuple[str, str | None], list[HouseRule]] = {}
    for old in removed:  # lowest number first
        by_words.setdefault(_words(old), []).append(old)
    moved: list[Moved] = []
    still_added: list[FileRule] = []
    gone: set[int] = set()
    for new in added:
        match = by_words.get((new.rule, new.supersedes))
        if match:
            old = match.pop(0)
            moved.append(Moved(old.number, new.number))
            gone.add(old.number)
        else:
            still_added.append(new)
    removed = [r for r in removed if r.number not in gone]
    return Diff(tuple(still_added), tuple(changed), tuple(removed), tuple(moved), same)


def _words(rule: HouseRule) -> tuple[str, str | None]:
    return _one_line(rule.rule), (_one_line(rule.supersedes or "") or None)


def _alike(mine: HouseRule, theirs: FileRule) -> bool:
    return _words(mine) == (theirs.rule, theirs.supersedes)


def filename(campaign_name: str) -> str:
    """`house-rules-frostmaiden.txt`: plain letters and digits only."""
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower().encode("ascii", "ignore").decode())
    slug = slug.strip("-")[:NAME_MAX].strip("-")
    return f"house-rules-{slug or 'campaign'}.txt"
