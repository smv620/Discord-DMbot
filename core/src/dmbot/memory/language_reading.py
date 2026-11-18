"""The languages heard in a session (#4229 slice 1d; Supervisor's ruling A on #4229). Pure: no
database, no AI call, nothing written.

The session reader is asked for them in the same call as its claims (`LANGUAGE_ADDENDUM`, so the
switch costs no extra call, as `met_reading` does). What it finds becomes a question for the DM,
as a lore upload's does for a character the campaign already has (#4229 1c, `ask_languages`): a
character with no sheet is asked "Does Okthandom speak Dwarvish?", one with a sheet is named to the
DM alone. Nothing is written by itself. Behind DMBOT_SESSION_LANGUAGES (off) until dev1's counts on
the invented `language_cases` text gate it.

Rules held in code, not trust:
- **Heard used, never only named.** The language must be in a cited line, said as spoken: "answers
  in Dwarvish", "speaks Elvish". Runes that are Elvish, a question ("does anyone speak Orc?") or a
  language a character can't speak ("doesn't speak Giant") are refused (`language_not_spoken`).
- **The language is the line's own word, or a plain mishearing of it**: the reader may give a known
  language for a word one or two letters off it ("Elfish" for Elvish), never a language the line
  doesn't hold (`language_not_in_line`).
- **One character, named.** Never a pronoun, never the party or a crowd, never "DM" or "Player".
- **The transcript is data.** Words that read like an instruction are refused.

Counts only leave this module; the words of a transcript are never logged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory.claims import LINES_MAX, reads_like_instruction
from dmbot.memory.models import NAME_MAX
from dmbot.memory.readings import PRONOUNS
from dmbot.memory.reveal_reading import GROUP_WORDS
from dmbot.names import not_names

MAX_PER_ANSWER = 10  # one batch of ten lines hears this many at most
LANGUAGE_WORDS = 4  # "Thieves' Cant" is a language; a sentence is not
PROMPT_SUFFIX = "+lang-1"  # added to the claims' prompt version when the addendum is sent

LANGUAGE_ADDENDUM = """

Also list the languages a character is heard SPEAKING in this stretch: someone answers, talks,
shouts, curses, sings or writes a note in a language, or the DM says they understand it as it is
spoken. Leave out a language only named (runes, a book, a sign "in Elvish"), a question about who
speaks one, and a language someone can't speak or doesn't know.
Give them as "languages", beside "claims":
{"claims": [...], "languages": [{"who": "Okthandom", "language": "Dwarvish", "lines": [2]}]}
"who" is the character's name, never "he", "she", "the party", "DM" or "Player". "language" is
the language's usual name; when the line's word is a plain mishearing of one ("Elfish"), give the
usual name ("Elvish"). Give "languages": [] if there are none."""

_LIST_START = re.compile(r'\s*\{\s*"claims"\s*:')
_VERBS = (
    r"speaks?|spoke|spoken|speaking|talks?|talked|talking|says?|said|answers?|answered|replies"
    r"|replied|mutters?|muttered|shouts?|shouted|curses?|cursed|sings?|sang|whispers?|whispered"
    r"|understands?|understood|knows?|knew|calls?|called|greets?|greeted|prays?|prayed"
)
# A speech verb right before the language: "answers her in Dwarvish", "speaks Elvish", "curses
# in Halfling". At most three words between, and none of them a thing that is only named.
_SPOKEN = re.compile(rf"\b(?:{_VERBS})\b(?P<between>(?:\W+[\w'’]+){{0,3}}?)\W+(?:in\W+)?$", re.I)
# A negation bound to speaking or knowing: "doesn't speak Giant", "can't understand Orc"
_NOT_SPOKEN = re.compile(
    r"\b(?:not|never|cannot|can't|can’t|don't|don’t|doesn't|doesn’t|didn't|didn’t|unable\s+to"
    r"|no\s+word\s+of)\W+(?:\w+\W+)?(?:speak|talk|know|understand|read)\w*\W+(?:\w+\W+){0,2}$",
    re.IGNORECASE,
)
# Things that are only named in a language, never heard: runes, a sign, a map's labels
_NAMED_ONLY = re.compile(
    r"\b(?:runes?|letters?|script|writing|written|sign|book|inscription|scroll|map|note|label)s?\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Heard:
    """One language a character was heard using: whose (as the reader named them), the language
    and the cited lines' numbers. No words of the transcript: the DM's question cites the session
    only, so none can be kept."""

    who: str
    language: str
    lines: tuple[str, ...]
    speakers: tuple[int, ...] = ()  # who spoke the lines (ids only), filled at the run's end


@dataclass(slots=True)
class Parsed:
    heard: list[Heard] = field(default_factory=list)
    refused: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1
        self.refused += 1


def _close(a: str, b: str) -> bool:
    """Two words of the same length one or two letters apart, of at least 5 letters: a plain
    mishearing ("Elfish" and "Elvish"), never a people ("dwarves", "elves", "giants") or two
    different languages."""
    a, b = a.casefold(), b.casefold()
    if a == b:
        return True
    if len(a) < 5 or len(a) != len(b):
        return False
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1] <= 2


def _where(language: str, text: str) -> int | None:
    """Where the language (or a plain mishearing of its one word) starts in the line, or None."""
    m = re.search(rf"\b{re.escape(language)}\b", text, re.IGNORECASE)
    if m is not None:
        return m.start()
    if " " in language.strip():
        return None
    for w in re.finditer(r"[A-Za-z’']+", text):
        if _close(w.group(), language):
            return w.start()
    return None


def _a_language(words: str) -> bool:
    plain = words.strip()
    if not plain or len(plain.split()) > LANGUAGE_WORDS:
        return False
    if plain.casefold() in not_names.LANGUAGES:
        return True
    return all(w[:1].isupper() for w in plain.split() if w.casefold() not in ("of", "the"))


def check(who: str, language: str, line: str) -> str:
    """Why this heard language is refused, or "" when it holds (pure; the rules above). Whose it is
    can't be checked against the line (a pronoun names nobody): the DM's question is that guard."""
    name = " ".join(who.split())
    if (
        not name
        or len(name) > NAME_MAX
        or name.casefold() in PRONOUNS
        or name.casefold() in GROUP_WORDS
        or name.casefold() in ("dm", "player", "the dm")
    ):
        return "language_who"
    if not _a_language(language):
        return "language_words"
    at = _where(language, line)
    if at is None:
        return "language_not_in_line"
    before = line[:at]
    clause_end = re.search(r"[.?!:;]", line[at:])
    if clause_end is not None and clause_end.group() == "?":
        return "language_not_spoken"  # (a question about it: "does anyone speak Orc?")
    if _NOT_SPOKEN.search(before):
        return "language_not_spoken"
    spoken = _SPOKEN.search(before)
    if spoken is None or _NAMED_ONLY.search(spoken.group("between") or ""):
        return "language_not_spoken"  # (named, not heard: "the runes are Elvish")
    return ""


def parse_languages(answer: str, lines: Mapping[str, str]) -> Parsed:
    """The languages in a claims answer that carries the addendum, each checked against the cited
    lines (`lines`: the batch's numbers to their words). Malformed parts are refused and counted;
    one character's language heard twice in an answer is one."""
    out = Parsed()
    text = answer.strip()
    start = _LIST_START.search(text)
    try:
        data: Any = json.loads(text[start.start() :] if start else text)
    except (json.JSONDecodeError, ValueError):
        return out
    items = data.get("languages") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    seen: set[tuple[str, str]] = set()
    for item in items[: MAX_PER_ANSWER * 2]:
        if len(out.heard) >= MAX_PER_ANSWER:
            out.drop("language_too_many")
            continue
        if not isinstance(item, dict):
            out.drop("language_malformed")
            continue
        who, language = str(item.get("who") or ""), str(item.get("language") or "")
        cited = item.get("lines")
        numbers = (
            tuple(str(n) for n in cited[:LINES_MAX] if str(n) in lines)
            if isinstance(cited, list)
            else ()
        )
        if not numbers:
            out.drop("language_lines")
            continue
        if reads_like_instruction(who) or reads_like_instruction(language):
            out.drop("language_instruction")
            continue
        why = ""
        holds = ""
        for n in numbers:
            why = check(who, language, lines[n])
            if not why:
                holds = n
                break
        if not holds:
            out.drop(why or "language_not_in_line")
            continue
        key = (" ".join(who.split()).casefold(), language.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        out.heard.append(Heard(" ".join(who.split()), language.strip(), numbers))
    return out


def once_each(found: Iterable[Heard]) -> list[Heard]:
    """One heard language per (character, language) across a session's batches: the first's name,
    with every batch's lines and speakers, so the question goes with any of their forgets
    (#4745)."""
    out: dict[tuple[str, str], Heard] = {}
    for h in found:
        key = (h.who.casefold(), h.language.casefold())
        first = out.get(key)
        out[key] = h if first is None else Heard(
            first.who, first.language,
            tuple(dict.fromkeys((*first.lines, *h.lines))),
            tuple(sorted({*first.speakers, *h.speakers})),
        )  # fmt: skip
    return list(out.values())
