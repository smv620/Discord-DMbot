"""Checking the cues in a recap's text (#2083): a pure function, so the website and Discord check
the same way.

A recap's `《mood》` and `((pacing))` cues steer the narration and the music (`audio/script.py`,
`audio/music.py`). Both ignore a word they do not know, so a typo in a cue silently made no
sound change. The DM now approves the text before any audio is made, and this check tells them
what to fix first: an unknown word (with the closest allowed one), a bracket never closed or
closed the wrong way, an empty cue, a mood with no narration after it, and a cue glued inside a
word. It never repairs anything: the DM fixes their own text. The allowed words come from the
tables the audio uses, so the list shown to the DM is the list that is checked.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from dmbot.recaps.audio.music import SYNONYMS
from dmbot.recaps.audio.script import EMOTION_VOICE, PERFORMANCE_WORDS

KINDS = ("unknown", "unclosed", "mismatched", "empty", "no_words", "inside_word")
MOODS = tuple(EMOTION_VOICE)  # the 18 mood words
PACING = tuple(PERFORMANCE_WORDS)  # pause, short pause, beat, long pause, hushed, whisper, ...
NEAR_WORDS = 3  # words of the text on each side that say where a problem is
MAX_CUE_CHARS = 60  # the longest a cue can be (`text.CUE`): longer is never read as a cue

_TOKEN = re.compile(r"《|》|\(\(|\)\)")
_CLOSER = {"《": "》", "((": "))"}
_WORDISH = re.compile(r"\w")


def allowed_words() -> dict[str, Any]:
    """What the check allows, for the website to show: the moods, the synonyms each mood also
    answers to, and the pacing words."""
    return {"moods": list(MOODS), "synonyms": dict(SYNONYMS), "pacing": list(PACING)}


@dataclass(frozen=True, slots=True)
class Problem:
    paragraph: int  # 1-based, counting paragraphs separated by a blank line
    near: str  # a few words around it
    cue: str  # the cue as written
    kind: str  # one of KINDS
    suggest: str | None = None  # the closest allowed word, for an unknown one

    def as_dict(self) -> dict[str, object]:
        return {
            "paragraph": self.paragraph,
            "near": self.near,
            "cue": self.cue,
            "kind": self.kind,
            "suggest": self.suggest,
        }

    def words(self) -> str:
        """The problem in plain words, with where it is."""
        where = f"Paragraph {self.paragraph}, near “{self.near}”"
        match self.kind:
            case "unknown":
                hint = f" Did you mean {self.suggest}?" if self.suggest else ""
                return f"{where}: {self.cue} is not a word DMbot knows.{hint}"
            case "unclosed":
                return f"{where}: {self.cue} is never closed."
            case "mismatched":
                return f"{where}: {self.cue} is closed with the wrong brackets."
            case "empty":
                return f"{where}: {self.cue} has nothing in it."
            case "no_words":
                return f"{where}: {self.cue} has no story after it before the next mood."
            case _:
                return f"{where}: {self.cue} is stuck inside a word. Put spaces around it."


def _closest(word: str, pool: tuple[str, ...]) -> str | None:
    found = difflib.get_close_matches(word, pool, n=1, cutoff=0.6)
    return found[0] if found else None


def _suggest(word: str, mood: bool) -> str | None:
    """The closest allowed word, a synonym too (it maps to a mood the DM may type as it is)."""
    pool = (*MOODS, *SYNONYMS) if mood else PACING
    return _closest(word, pool)


def _near(text: str, start: int, end: int) -> str:
    """A few words either side of the cue (the cue itself left out)."""
    before = text[:start].split()[-NEAR_WORDS:]
    after = text[end:].split()[:NEAR_WORDS]
    return " ".join([*before, *after]) or text[start:end]


def _paragraphs(summary: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", summary) if p.strip()]


def _check_paragraph(number: int, text: str) -> tuple[list[Problem], list[tuple[int, int, str]]]:
    """The problems in one paragraph, and where its good mood cues are: (start, end, as written)."""
    problems: list[Problem] = []
    moods: list[tuple[int, int, str]] = []
    tokens = list(_TOKEN.finditer(text))
    i = 0
    while i < len(tokens):
        opener = tokens[i]
        if opener.group() in ("》", "))"):  # a closer with nothing open
            problems.append(
                Problem(
                    number, _near(text, opener.start(), opener.end()), opener.group(), "unclosed"
                )
            )
            i += 1
            continue
        want = _CLOSER[opener.group()]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is None or nxt.group() in _CLOSER:  # another opener, or the text ends
            written = text[opener.start() : opener.end() + MAX_CUE_CHARS].split("\n")[0]
            problems.append(
                Problem(number, _near(text, opener.start(), opener.end()), written[:20], "unclosed")
            )
            i += 1
            continue
        written = text[opener.start() : nxt.end()]
        if nxt.group() != want:
            problems.append(
                Problem(number, _near(text, opener.start(), nxt.end()), written, "mismatched")
            )
            i += 2
            continue
        inner = text[opener.end() : nxt.start()]
        word = " ".join(inner.lower().split())
        is_mood = opener.group() == "《"
        near = _near(text, opener.start(), nxt.end())
        if not word:
            problems.append(Problem(number, near, written, "empty"))
        elif len(inner) > MAX_CUE_CHARS or (
            word not in (MOODS if is_mood else PACING) and not (is_mood and word in SYNONYMS)
        ):
            problems.append(Problem(number, near, written, "unknown", _suggest(word, is_mood)))
        else:
            before = text[opener.start() - 1] if opener.start() else ""
            after = text[nxt.end()] if nxt.end() < len(text) else ""
            if _WORDISH.match(before) and _WORDISH.match(after):
                problems.append(Problem(number, near, written, "inside_word"))
            elif is_mood:
                moods.append((opener.start(), nxt.end(), written))
        i += 2
    return problems, moods


def cue_problems(summary: str) -> list[Problem]:
    """Everything wrong with the cues in this text, in the order it appears. Empty when clean."""
    found: list[Problem] = []
    paragraphs = _paragraphs(summary)
    joined = "\n\n".join(paragraphs)
    # (paragraph, start and end in the whole text, start in the paragraph, the cue, the paragraph)
    marks: list[tuple[int, int, int, int, str, str]] = []
    base = 0
    for number, text in enumerate(paragraphs, start=1):
        problems, moods = _check_paragraph(number, text)
        found += problems
        marks += [(number, base + a, base + b, a, w, text) for a, b, w in moods]
        base += len(text) + 2
    # A mood with no narration after it before the next mood (in any paragraph) or the end.
    pacing = re.compile(r"\(\([^()]*\)\)")
    for n, (number, _start, end, local, written, text) in enumerate(marks):
        stop = marks[n + 1][1] if n + 1 < len(marks) else len(joined)
        story = _TOKEN.sub("", pacing.sub("", joined[end:stop]))
        if not story.strip():
            found.append(
                Problem(number, _near(text, local, local + len(written)), written, "no_words")
            )
    return found
