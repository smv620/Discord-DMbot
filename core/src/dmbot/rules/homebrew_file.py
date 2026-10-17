"""The homebrew file: a campaign's homebrew as plain text a table can keep anywhere (#1524).
Pure: no Discord, no database.

One entry per block. A block starts with `## Name (kind)` on its own line; then, optionally and
in this order, `Summary: …` and `Off: yes` (an entry the DM turned off; #1629); then a blank
line; then the words, which may run over many lines:

    Homebrew: Frostmaiden
    # Lines starting with one # before the first entry are notes and are ignored.

    ## Hollow King (monster)
    Summary: A crowned skeleton.
    Off: yes

    A skeleton in a rusted crown. It cannot be touched while it wears the crown.

An entry that came from a shared rulebook has one more line under its heading, `[Shared book]`
(and `[Legacy 2014]` too when the book is for the older rules and the campaign plays the newer),
the same tags the players' card carries (#1584). It is a note for whoever reads the file: `parse`
accepts it and ignores it, so an entry loaded back is the campaign's own and never from a book;
only the "I have the right" step marks an entry as from a book.

`parse` and `write` go both ways without loss. A line of the words that starts with `##` is
written with one more backslash in front (`\\##`) and read back without it, so the words can
never be taken for the next entry. A block that is not an entry is reported, never guessed at.
The format is the same as the house-rules file's in spirit (`dmbot.rules.house_file`): a copy to
keep, edit and load back; loading checks everything again (the store's limits apply).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dmbot.rules.book_rank import BOOK_TAG, LEGACY_TAG, book_tag
from dmbot.rules.homebrew import (
    HOMEBREW_MAX,
    KINDS,
    NAME_MAX,
    NOT_HOMEBREW,
    RULE_KIND,
    SUMMARY_MAX,
    TEXT_MAX,
    FileEntry,
    Homebrew,
    HomebrewError,
    clean_name,
    clean_summary,
    clean_text,
    name_key,
)

MAX_FILE_CHARS = 700_000  # 200 entries of 2,000 characters and their headings, with room to spare
MAX_LINE = 4_000
MAX_PROBLEMS = 20
HELP = (
    "# How this file works\n"
    "# Each entry starts with: ## Name (kind)\n"
    f"# Kinds: {', '.join(KINDS)}\n"
    "# Under the heading you can add:  Summary: one short line\n"
    "#                                 Off: yes   (DMbot ignores the entry until you turn it on)\n"
    "# Then a blank line, then the words.\n"
    "# Lines starting with a single # before the first entry are notes and are ignored.\n"
    "# A line like [Shared book] under a heading is a note from DMbot; leave it or delete it."
)
EXAMPLE = (
    "## Hollow King (monster)\n"
    "Summary: A crowned skeleton.\n"
    "Off: no\n"
    "\n"
    "Write what it does here, in your own words."
)
NAME_FILE_MAX = 40

TOO_BIG = (
    f"the file is too big (over {MAX_FILE_CHARS:,} characters). Nothing was read. Try splitting it"
)
NOT_AN_ENTRY = "this isn't under an entry. Start one with ## Name (kind)"
NO_KIND = f"add a kind in brackets after the name, like (monster). Kinds: {', '.join(KINDS)}"
BAD_KIND = f"that kind isn't one DMbot knows. Use: {', '.join(KINDS)}"
NO_NAME = "the heading has no name. Write ## Name (kind)"
NAME_TOO_LONG = f"the name is over {NAME_MAX} characters"
NO_TEXT = "add the words after a blank line under the heading"
TEXT_TOO_LONG = f"the words are over {TEXT_MAX} characters"
SUMMARY_TOO_LONG = f"the summary is over {SUMMARY_MAX} characters"
BAD_OFF = "write Off: yes  or  Off: no (or leave the line out)"
TWICE = "another entry already has this name, so this one was skipped"
TOO_MANY = f"more than {HOMEBREW_MAX} entries; the rest are left out"
LINE_TOO_LONG = f"a line is over {MAX_LINE} characters. Split it into shorter lines"

MAX_HEADING = 300  # a longer heading is not an entry (and is never matched)
_ESCAPED = re.compile(r"^(\\+)(##.*)$")
_UNESCAPE = re.compile(r"^\\(\\*##.*)$")


@dataclass(frozen=True, slots=True)
class Problem:
    line: int  # counted from 1
    text: str  # the line, cut short enough to show
    why: str


@dataclass(frozen=True, slots=True)
class Parsed:
    entries: tuple[FileEntry, ...]
    problems: tuple[Problem, ...]


def _escape(line: str) -> str:
    return f"\\{line}" if line.startswith("##") or _ESCAPED.match(line) else line


def _unescape(line: str) -> str:
    match = _UNESCAPE.match(line)
    return match.group(1) if match else line


# The only lines `parse` skips under a heading: a tag note `write` adds (#1584).
_TAG_LINES = frozenset({BOOK_TAG, f"{BOOK_TAG} {LEGACY_TAG}"})


def write(
    campaign_name: str,
    entries: Sequence[Homebrew],
    target_ruleset: str,
    *,
    legacy: Callable[[Homebrew], bool] | None = None,
) -> str:
    """The file for these entries (this campaign's), by name. An entry from a shared rulebook
    carries its tags under the heading; `target_ruleset` is the one the campaign plays, which
    decides `[Legacy 2014]`. The website has no ruleset to hand, only its door's answer for each
    entry (the same test, worked out in the database), so it passes `legacy` instead: then the
    two files are byte-equal for the same entries (#1593)."""
    title = " ".join(campaign_name.split()) or "Campaign"
    out = [f"Homebrew: {title}", HELP]
    if not entries:  # a template to start from
        out += ["", *(f"# {line}" if line else "#" for line in EXAMPLE.split("\n"))]
    for entry in sorted(entries, key=lambda e: name_key(e.name)):
        out += ["", f"## {entry.name} ({entry.kind})"]
        tag = book_tag(entry, target_ruleset, None if legacy is None else legacy(entry))
        if tag is not None:
            out.append(tag)
        if entry.summary:
            out.append(f"Summary: {entry.summary}")
        if not entry.enabled:  # (on is the default and is not written)
            out.append("Off: yes")
        out.append("")
        out += [_escape(line) for line in entry.text.split("\n")]
    return "\n".join(out) + "\n"


def parse(text: str) -> Parsed:
    """The entries in a file, and the blocks and lines that could not be read. Never raises, and
    costs time in proportion to the file: it is cut off at `MAX_FILE_CHARS`, and at most
    `MAX_PROBLEMS` are listed (then a count)."""
    if len(text) > MAX_FILE_CHARS:
        return Parsed((), (Problem(0, "", TOO_BIG),))
    entries: dict[str, FileEntry] = {}
    problems: list[Problem] = []
    unlisted = 0

    def report(n: int, line: str, why: str) -> None:
        nonlocal unlisted
        if len(problems) < MAX_PROBLEMS:
            problems.append(Problem(n, _shown(line), why))
        else:
            unlisted += 1

    # Cut into blocks first: (line number of the heading, heading, the lines after it).
    blocks: list[tuple[int, str, list[str]]] = []
    title_seen = False
    for n, raw in enumerate(text.lstrip("﻿").replace("\r\n", "\n").split("\n"), 1):
        if len(raw) > MAX_LINE:
            report(n, raw, LINE_TOO_LONG)
            raw = ""
        if raw.startswith("##"):
            blocks.append((n, raw, []))
            continue
        if blocks:
            blocks[-1][2].append(raw)
            continue
        line = raw.strip()  # before the first entry: a title, notes, blank lines
        if not line or line.startswith("#"):
            continue
        if not title_seen:
            title_seen = True  # the first such line is the title
        else:
            report(n, line, NOT_AN_ENTRY)

    for n, heading, lines in blocks:
        if len(entries) >= HOMEBREW_MAX:
            report(n, heading, TOO_MANY)
            break
        found = _entry(n, heading, lines, report)
        if found is None:
            continue
        key = name_key(found.name)
        if key in entries:
            report(n, heading, TWICE)
        else:
            entries[key] = found
    if unlisted:
        problems.append(Problem(0, "", f"…and {unlisted} more problems"))
    return Parsed(tuple(entries.values()), tuple(problems))


def _entry(
    n: int, heading: str, lines: list[str], report: Callable[[int, str, str], None]
) -> FileEntry | None:

    def bad(why: str) -> None:
        report(n, heading, why)

    name, bracket = _heading(heading)
    kind = (bracket or "").strip().lower()
    if not name:
        bad(NO_NAME)
        return None
    if bracket is None:
        bad(NO_KIND)
        return None
    if kind == RULE_KIND:  # a rule is a house rule (#1628): this entry only is left out
        bad(NOT_HOMEBREW)
        return None
    if kind not in KINDS:
        bad(BAD_KIND)
        return None
    # The lines up to the first blank one are the optional Summary and Players lines.
    summary: str | None = None
    switch: bool | None = None  # (None: the file doesn't say)
    i = 0
    while i < len(lines) and lines[i].strip():
        if i == 0 and lines[0].strip() in _TAG_LINES:
            i += 1  # a note for the reader: never read as a fact about the entry
            continue
        label, colon, value = lines[i].partition(":")
        key = label.strip().lower()
        if colon and key == "summary":
            summary = value.strip()
        elif colon and key == "off":
            answer = value.strip().lower()
            if answer not in ("yes", "no"):
                bad(BAD_OFF)
                return None
            switch = answer == "no"  # (the entry is on)
        elif colon and key == "players may know":
            # An older file's line: nothing is hidden from players any more (part 2). Whatever it
            # says is ignored, so an old file never stops on a setting that no longer exists.
            pass
        else:
            break  # words with no blank line before them: they are the words
        i += 1
    words = "\n".join(_unescape(line) for line in lines[i:])
    try:
        clean_name_ = clean_name(name)
        text = clean_text(words)
        shortened = clean_summary(summary)
    except HomebrewError as exc:
        bad(_why(str(exc)))
        return None
    return FileEntry(kind, clean_name_, text, shortened, switch)


def _heading(line: str) -> tuple[str, str | None]:
    """(name, kind in brackets or None) from a `## Name (kind)` line, by plain string work: a
    pattern with nested whitespace quantifiers can take minutes on a line of spaces (an untrusted
    file must never stall the bot)."""
    body = line[2:].strip()
    if len(body) > MAX_HEADING:
        return body[:MAX_HEADING].strip(), None
    if body.endswith(")") and "(" in body:
        name, _, rest = body[:-1].rpartition("(")
        if "(" not in rest and ")" not in rest:
            return name.strip(), rest
    return body, None


def _why(message: str) -> str:
    if "words" in message or "text box was empty" in message:
        return NO_TEXT
    if "too long" in message and "name" in message:
        return NAME_TOO_LONG
    if "summary" in message:
        return SUMMARY_TOO_LONG
    if "too long" in message:
        return TEXT_TOO_LONG
    return "DMbot couldn't read this entry"


def _shown(line: str) -> str:
    return line if len(line) <= 80 else line[:79] + "…"


def filename(campaign_name: str) -> str:
    """`homebrew-frostmaiden.txt`: plain letters and digits only."""
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower().encode("ascii", "ignore").decode())
    slug = slug.strip("-")[:NAME_FILE_MAX].strip("-")
    return f"homebrew-{slug or 'campaign'}.txt"
