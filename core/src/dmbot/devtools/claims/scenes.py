"""Reads the golden set (docs/test-scripts/story-scenes.md): scenes of numbered,
speaker-labelled lines, each with the claims a person would pull from it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HOWS = ("dm_said", "player_said", "plan")
WORDS_PER_SECOND = 2.5  # talk at a table, about 150 words a minute

_SCENE = re.compile(r"^##\s+Scene\s+\d+:\s*(.+)$")
_LINE = re.compile(r"^(\d+)\.\s+\[(DM|Player)\]\s+(.+)$")
_EXPECT = re.compile(r"^-\s+(dm_said|player_said|plan|also):\s*(.+)$")
_NEVER = re.compile(r"^-\s+never:\s*(.+)$")


@dataclass(frozen=True, slots=True)
class Expected:
    how: str
    subject: str
    relationship: str
    object: str


@dataclass(slots=True)
class Line:
    number: int
    speaker: str  # "DM" or "Player"
    text: str
    expected: list[Expected] = field(default_factory=list)
    also: list[Expected] = field(default_factory=list)  # fine to pull, not required
    never: list[tuple[str, ...]] = field(default_factory=list)


@dataclass(slots=True)
class Scene:
    title: str
    lines: list[Line] = field(default_factory=list)

    @property
    def talk_s(self) -> float:
        """About how long the scene takes to say."""
        return sum(len(line.text.split()) for line in self.lines) / WORDS_PER_SECOND


def parse_scenes(text: str) -> list[Scene]:
    scenes: list[Scene] = []
    line: Line | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if match := _SCENE.match(stripped):
            scenes.append(Scene(match.group(1)))
            line = None
        elif not scenes:
            continue
        elif match := _LINE.match(stripped):
            line = Line(int(match.group(1)), match.group(2), match.group(3))
            scenes[-1].lines.append(line)
        elif line is None or not stripped:
            continue
        elif match := _EXPECT.match(stripped):
            parts = [p.strip() for p in match.group(2).split("|")]
            if len(parts) != 3 or not all(parts):
                raise ValueError(f"line {line.number}: expected 'subject | relationship | object'")
            how = match.group(1)
            (line.also if how == "also" else line.expected).append(Expected(how, *parts))
        elif match := _NEVER.match(stripped):
            line.never.append(tuple(w.strip().casefold() for w in match.group(1).split(",")))
        elif stripped == "- none":
            continue
        elif not stripped.startswith("-"):
            line.text += " " + stripped  # a line that wraps
    if not scenes or not any(s.lines for s in scenes):
        raise ValueError("no '## Scene N: …' sections with numbered lines found")
    return scenes


def batches(scenes: list[Scene], min_s: float) -> list[Scene]:
    """Consecutive scenes joined until each batch is at least `min_s` of talk, as live
    batches are (#234: 30 to 60 s); a batch may span a change of scene, as live."""
    out: list[Scene] = []
    for scene in scenes:
        if out and out[-1].talk_s < min_s:
            out[-1] = Scene(f"{out[-1].title} + {scene.title}", [*out[-1].lines, *scene.lines])
        else:
            out.append(Scene(scene.title, list(scene.lines)))
    return out


def load_scenes(path: Path) -> list[Scene]:
    return parse_scenes(path.read_text(encoding="utf-8"))
