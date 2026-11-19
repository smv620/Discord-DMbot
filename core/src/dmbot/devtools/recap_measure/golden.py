"""Reads the invented golden set for the recap (#1382): dmbot/devtools/golden/recap/*.json.

Each file is one invented session: its lines (`dm`, or a player letter), the confirmed names, a
secret name that must never appear, the beats a good sheet holds, each as a few key words, and
`characters` (each player letter's one character). Each is written like a real table (owner,
2026-11-19; `docs/RECAP_STANDARD.md`): the DM describes what the senses meet and each new person
when first met, each player speaks only for their own character, the DM decides outcomes, and
speech is in quotation marks. No real person, no live transcript, no sourcebook text.
`load_golden` refuses anything malformed, and a test runs it over the files in CI, so a typo
cannot quietly weaken the measurement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from dmbot.transcript.models import Line

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "recap"
DM_ID = 1
PLAYER_IDS = {"a": 2, "b": 3, "c": 4}
MIN_LINES, MAX_LINES = 8, 30


class GoldenError(ValueError):
    """A file that does not follow the format; the message says which."""


@dataclass(frozen=True, slots=True)
class Session:
    name: str
    title: str
    lines: tuple[Line, ...]
    names: tuple[str, ...]
    secret: tuple[str, ...]
    reference: tuple[tuple[str, ...], ...]  # key words of each beat a good sheet holds
    window_chars: int = 40_000  # a small number makes a short session several windows
    characters: tuple[tuple[str, str], ...] = ()  # (player letter, the character they play)


def speaks_for_another(text: str, own: str, characters: dict[str, str]) -> str | None:
    """The other player character a player's line opens with (a player declaring someone else's
    action, which the owner's rule forbids), or None. The cheapest form of the rule: a guard on
    how the line starts, not a grammar check."""
    for character in characters.values():
        if character != own and re.match(rf"\s*{re.escape(character)}\b", text, re.IGNORECASE):
            return character
    return None


def parse(name: str, raw: object) -> Session:
    if not isinstance(raw, dict):
        raise GoldenError(f"{name}: not an object")
    try:
        rows = raw["lines"]
        names = tuple(str(n) for n in raw["names"])
        secret = tuple(str(n) for n in raw["secret"])
        beats = tuple(tuple(str(k).lower() for k in b["keys"]) for b in raw["beats"])
        title = str(raw["title"])
    except (KeyError, TypeError) as exc:
        raise GoldenError(f"{name}: missing or wrong field ({exc!r})") from None
    if not isinstance(rows, list) or not MIN_LINES <= len(rows) <= MAX_LINES:
        raise GoldenError(f"{name}: needs {MIN_LINES} to {MAX_LINES} lines")
    characters = raw.get("characters", {})
    if not isinstance(characters, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in characters.items()
    ):
        raise GoldenError(f"{name}: characters must map each player letter to a name")
    lines: list[Line] = []
    for n, row in enumerate(rows, start=1):
        if not (isinstance(row, list) and len(row) == 2 and isinstance(row[1], str) and row[1]):
            raise GoldenError(f"{name}: line {n} is not [speaker, text]")
        who = DM_ID if row[0] == "dm" else PLAYER_IDS.get(str(row[0]))
        if who is None:
            raise GoldenError(f"{name}: line {n} has an unknown speaker")
        if characters and who != DM_ID:
            own = characters.get(str(row[0]))
            if own is None:
                raise GoldenError(f"{name}: player {row[0]} has no character in `characters`")
            other = speaks_for_another(row[1], own, characters)
            if other is not None:
                raise GoldenError(
                    f"{name}: line {n} opens with {other}, another player's character: "
                    "each player speaks only for their own"
                )
        lines.append(Line(n * 1000, who, row[1], row[1]))
    if len(beats) < 3 or any(not b for b in beats):
        raise GoldenError(f"{name}: needs at least three beats with key words")
    if not secret:
        raise GoldenError(f"{name}: needs a secret name to test leaks against")
    size = raw.get("window_chars", 40_000)
    if type(size) is not int or not 200 <= size <= 40_000:
        raise GoldenError(f"{name}: window_chars must be a whole number from 200 to 40000")
    return Session(
        name, title, tuple(lines), names, secret, beats, size, tuple(sorted(characters.items()))
    )


def missing(folder: Path) -> str:
    """Plain words for a golden folder that is not there (it ships inside the package)."""
    return (
        f"The golden data folder isn't there: {folder}. It is part of DMbot's package "
        "(dmbot/devtools/golden); install DMbot again."
    )


def load_golden(folder: Path = GOLDEN) -> list[Session]:
    if not folder.is_dir():
        raise GoldenError(missing(folder))
    files = sorted(folder.glob("*.json"))
    if not files:
        raise GoldenError(f"{folder}: no sessions")
    return [parse(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in files]
