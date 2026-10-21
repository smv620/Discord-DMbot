"""Reads and checks the golden set for the story-memory extractor (#1268).

The set is invented table talk in dmbot/devtools/golden/story/*.txt: no real person, no live
transcript, no sourcebook text. Every line says who spoke (`dm` or `player`) and what kind of
line it is, and under it what a person would pull from it. The format is deliberately plain so
a developer can add a scene by hand; `load_golden` refuses anything malformed, and a test runs
it over the files in CI, so a typo cannot quietly weaken the measurement.

    ## Scene 12: Title
    1. dm statement: Captain Roan falls into the gorge and does not come up.
       - state: Captain Roan | dead
    2. player plan: Let's kill the king.
       - claim: party | kill | king
       - never: king, dead
    3. player question: Can I roll Insight?

Kinds: `statement` (said as fact or belief), `question`, `plan` (something meant to be done, or
a "what if") and `ooc` (table talk, dice, rules). Bullets: `claim: subject | relationship |
object`, `state: subject | condition` (a change of state), `also:` (fine to pull, not required)
and `never: word, word` (an injection line: no claim may use all of these words). Only a
statement or a plan can yield a claim. How a claim was said follows from the line: a DM
statement is `dm_said` (an NPC's lie included), a player statement `player_said`, a plan
`plan`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from dmbot.devtools.claims.scenes import Expected, Line, Scene
from dmbot.devtools.claims.score import words as tokens

# The conditions a state change can name (docs/STORY_MEMORY.md, "State facts"). Kept here, not
# imported, so the golden set doesn't depend on the memory code it is there to measure.
CONDITIONS = (
    "alive", "dead", "missing", "captured", "transformed", "unknown", "intact", "destroyed", "lost",
)  # fmt: skip
SPEAKERS = {"dm": "DM", "player": "Player"}
KINDS = ("statement", "question", "plan", "ooc")
MIN_LINES, MAX_LINES = 5, 15  # a scene is a minute or so of talk
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "story"

_SCENE = re.compile(r"^##\s+Scene\s+(\d+):\s*(.+)$")
_LINE = re.compile(r"^(\d+)\.\s+(dm|player)\s+(statement|question|plan|ooc):\s*(.+)$")
_BULLET = re.compile(r"^-\s+(claim|state|also|never):\s*(.+)$")


class GoldenError(ValueError):
    """A file that doesn't follow the format; the message says where."""


def how_said(speaker: str, kind: str) -> str:
    """How a claim from this line was said: what the extractor must call it."""
    return "plan" if kind == "plan" else ("dm_said" if speaker == "dm" else "player_said")


def _parts(text: str, count: int, where: str) -> list[str]:
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != count or not all(parts):
        shape = "subject | relationship | object" if count == 3 else "subject | condition"
        raise GoldenError(f"{where}: expected '{shape}'")
    return parts


def parse_golden(text: str, name: str = "golden") -> list[Scene]:
    """The scenes in one file. Raises GoldenError, naming the line, on anything wrong."""
    scenes: list[Scene] = []
    line: Line | None = None
    speaker = ""
    for n, raw in enumerate(text.splitlines(), 1):
        where = f"{name}:{n}"
        stripped = raw.strip()
        if not stripped:
            continue
        if match := _SCENE.match(stripped):
            _check_scene(scenes, name)
            scenes.append(Scene(f"{match.group(1)}: {match.group(2)}"))
            line = None
        elif not scenes:
            raise GoldenError(f"{where}: text before the first '## Scene N: title'")
        elif match := _LINE.match(stripped):
            number = int(match.group(1))
            if number != len(scenes[-1].lines) + 1:
                raise GoldenError(f"{where}: lines count 1, 2, 3… in each scene")
            speaker, kind = match.group(2), match.group(3)
            line = Line(number, SPEAKERS[speaker], match.group(4), kind)
            scenes[-1].lines.append(line)
        elif match := _BULLET.match(stripped):
            if line is None:
                raise GoldenError(f"{where}: a claim with no line above it")
            _add_bullet(line, speaker, match.group(1), match.group(2), where)
        elif stripped.startswith("-"):
            raise GoldenError(f"{where}: unknown bullet (claim, state, also or never)")
        elif line is not None:
            line.text += " " + stripped  # a line that wraps
        else:
            raise GoldenError(f"{where}: text outside a numbered line")
    _check_scene(scenes, name)
    if not scenes:
        raise GoldenError(f"{name}: no scenes")
    return scenes


def _add_bullet(line: Line, speaker: str, label: str, body: str, where: str) -> None:
    if label == "never":
        # Single words, as the scoring compares them: "king of dunmere" is king and dunmere.
        words = tuple(sorted({t for part in body.split(",") for t in tokens(part)}))
        if not words:
            raise GoldenError(f"{where}: 'never' needs words")
        line.never.append(words)
        return
    if line.kind not in ("statement", "plan"):
        raise GoldenError(f"{where}: a {line.kind} yields no claim")
    how = how_said(speaker, line.kind)
    if label == "state":
        subject, condition = _parts(body, 2, where)
        if condition not in CONDITIONS:
            raise GoldenError(
                f"{where}: '{condition}' is not a condition ({', '.join(CONDITIONS)})"
            )
        line.expected.append(Expected(how, subject, "is", condition, condition=True))
        return
    expected = Expected(how, *_parts(body, 3, where))
    (line.also if label == "also" else line.expected).append(expected)


def _check_scene(scenes: list[Scene], name: str) -> None:
    """The scene just finished is the right size and has something to find."""
    if not scenes:
        return
    scene = scenes[-1]
    if not MIN_LINES <= len(scene.lines) <= MAX_LINES:
        raise GoldenError(f"{name}: scene {scene.title!r} has {len(scene.lines)} lines")
    for line in scene.lines:
        if line.kind == "question" and "?" not in line.text:
            raise GoldenError(
                f"{name}: scene {scene.title!r} line {line.number}: a question has no '?'"
            )
    # Nothing is expected that no extractor could read: every name in a claim is in the scene's
    # own words, whichever line (a perfect-extractor test just echoes the claims back, so it
    # can't see this). "party" stands for the player characters and is always fine.
    said: set[str] = set()
    for line in scene.lines:
        said |= tokens(line.text)
    for line in scene.lines:
        for e in (*line.expected, *line.also):
            parts = [e.subject] if e.condition else [e.subject, e.object]
            for part in parts:
                options = part.split("/")
                if part.casefold() != "party" and not any(tokens(o) & said for o in options):
                    raise GoldenError(
                        f"{name}: scene {scene.title!r} line {line.number}: '{part}' is not "
                        "in the scene's words"
                    )


def load_golden(directory: Path = GOLDEN) -> list[Scene]:
    """Every scene of every *.txt file in the folder, in file order. Scene numbers must run 1,
    2, 3… without a gap or repeat across the files."""
    if not directory.is_dir():
        raise GoldenError(
            f"The golden data folder isn't there: {directory}. It is part of DMbot's package "
            "(dmbot/devtools/golden); install DMbot again."
        )
    paths = sorted(directory.glob("*.txt"))
    if not paths:
        raise GoldenError(f"no *.txt files in {directory}")
    scenes: list[Scene] = []
    for path in paths:
        scenes += parse_golden(path.read_text(encoding="utf-8"), path.name)
    for expected, scene in enumerate(scenes, 1):
        if not scene.title.startswith(f"{expected}:"):
            raise GoldenError(
                f"scene numbers must run 1, 2, 3…: found '{scene.title}' at {expected}"
            )
    return scenes


def summary(scenes: Iterable[Scene]) -> str:
    scenes = list(scenes)
    lines = [line for s in scenes for line in s.lines]
    kinds = {k: sum(line.kind == k for line in lines) for k in KINDS}
    claims = sum(len(line.expected) for line in lines)
    states = sum(e.condition for line in lines for e in line.expected)
    traps = sum(bool(line.never) for line in lines)
    said = ", ".join(f"{n} {k}" for k, n in kinds.items())
    return (
        f"{len(scenes)} scenes, {len(lines)} lines ({said}); {claims} expected claims, of which "
        f"{states} state changes; {traps} lines with a never-claim (injections and plans that "
        "are not facts)"
    )
