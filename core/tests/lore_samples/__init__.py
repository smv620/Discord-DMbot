"""Invented campaign-lore documents with a hand-written expected answer for each statement: which
statements every inhabitant would know ("world") and which the player characters would have to
discover ("spoiler"). They test the smart-guess choice (#2959) and call no AI. Names and places are
made up; nothing here comes from a sourcebook or an adventure."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

HERE = Path(__file__).parent

Expect = Literal["world", "spoiler"]
_EXPECTS: tuple[Expect, ...] = ("world", "spoiler")


@dataclass(frozen=True)
class PcBackstory:
    """A player character and the one-line tie that makes some spoilers known to them."""

    character: str
    tie: str


@dataclass(frozen=True)
class Statement:
    """One statement from a document. `world_for` lists the characters for whom a spoiler is known
    because of their backstory; it is empty otherwise."""

    text: str
    expect: Expect
    world_for: tuple[str, ...]


@dataclass(frozen=True)
class Sample:
    """One invented document with its expected answers."""

    name: str
    text: str
    title: str
    pc_backstories: tuple[PcBackstory, ...]
    statements: tuple[Statement, ...]


def load_all() -> list[Sample]:
    """Every sample in this folder, sorted by name. A file that breaks the shape raises at once, so
    a bad sample never passes silently."""
    samples = []
    for path in sorted(HERE.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        statements = []
        for item in data["statements"]:
            expect = item["expect"]
            if expect not in _EXPECTS:
                raise ValueError(f"{path.name}: unknown expect {expect!r}")
            statements.append(
                Statement(
                    text=item["text"],
                    expect=expect,
                    world_for=tuple(item["world_for"]),
                )
            )
        samples.append(
            Sample(
                name=path.stem,
                text=path.with_suffix(".txt").read_text(encoding="utf-8"),
                title=data["title"],
                pc_backstories=tuple(
                    PcBackstory(character=pc["character"], tie=pc["tie"])
                    for pc in data["pc_backstories"]
                ),
                statements=tuple(statements),
            )
        )
    return samples
