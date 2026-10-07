"""Reads a read-aloud test script (docs/test-scripts/*.md) into the words that should be
heard: who says each one, which part it scores in, and where the dramatic pauses are.

The format is the one in docs/test-scripts/README.md: `**[DM]:**` or `**[Player]:**`
starts a turn, `## Part 1` and `## Part 2` mark the parts, `(( dramatic pause ))` and
`(( whispering ))` are stage directions, and the whispered sentence is in *italics*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Part(StrEnum):
    ONE = "part 1"
    TWO = "part 2"
    WHISPER = "whisper"


@dataclass(frozen=True, slots=True)
class Word:
    text: str  # as written in the script
    speaker: str  # "DM" or "Player"
    part: Part
    after_pause: bool  # the first word after a (( dramatic pause ))


@dataclass(frozen=True, slots=True)
class Script:
    name: str  # "dm-only", from the file name
    words: tuple[Word, ...]
    turns: int = 0  # [DM] and [Player] turns

    @property
    def pauses(self) -> int:
        return sum(w.after_pause for w in self.words)

    def count(self, part: Part) -> int:
        return sum(w.part is part for w in self.words)


_TURN = re.compile(r"^\*\*\[(DM|Player)\]:\*\*\s*(.*)$")
_PART = re.compile(r"^##\s+Part\s+([12])\b", re.IGNORECASE)
_DIRECTION = re.compile(r"\(\(\s*([^)]*?)\s*\)\)")
_ITALIC = re.compile(r"\*([^*]+)\*")
_TOKEN = re.compile(r"\S+")
_PAUSE = "\x00pause\x00"
_WHISPER_ON = "\x00whisper\x00"
_WHISPER_OFF = "\x00/whisper\x00"


def parse_script(text: str, name: str = "script") -> Script:
    """The scored words of a script, in reading order."""
    part: Part | None = None
    turns: list[tuple[str, Part, list[str]]] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if match := _PART.match(line):
            part = Part.ONE if match.group(1) == "1" else Part.TWO
            current = None
            continue
        if not line or line.startswith("#"):
            current = None
            continue
        if match := _TURN.match(line):
            if part is None:
                raise ValueError(f"{name}: a turn before '## Part 1'")
            current = [match.group(2)]
            turns.append((match.group(1), part, current))
        elif line.startswith("**["):
            current = None  # a note such as [Don't read this out loud.] ends the turn
        elif current is not None:
            current.append(line)
    words: list[Word] = []
    for speaker, turn_part, lines in turns:
        words.extend(_turn_words(" ".join(lines), speaker, turn_part))
    if not words:
        raise ValueError(f"{name}: no [DM] or [Player] lines found")
    return Script(name=name, words=tuple(words), turns=len(turns))


def load_script(path: Path) -> Script:
    return parse_script(path.read_text(encoding="utf-8"), name=path.stem)


def _turn_words(text: str, speaker: str, part: Part) -> list[Word]:
    def direction(match: re.Match[str]) -> str:
        what = match.group(1).casefold()
        if what == "dramatic pause":
            return f" {_PAUSE} "
        if what == "whispering":
            return f" {_WHISPER_ON} "
        return " "

    marked = _DIRECTION.sub(direction, text)
    # The whispered sentence is the italic text after (( whispering )).
    marked = re.sub(
        rf"{re.escape(_WHISPER_ON)}\s*\*([^*]+)\*",
        lambda m: f" {_WHISPER_ON} {m.group(1)} {_WHISPER_OFF} ",
        marked,
    )
    marked = _ITALIC.sub(r"\1", marked).replace("**", "")
    words: list[Word] = []
    after_pause = False
    whispering = False
    for token in _TOKEN.findall(marked):
        if token == _PAUSE:
            after_pause = True
        elif token == _WHISPER_ON:
            whispering = True
        elif token == _WHISPER_OFF:
            whispering = False
        else:
            words.append(Word(token, speaker, Part.WHISPER if whispering else part, after_pause))
            after_pause = False
    return words
