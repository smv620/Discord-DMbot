"""How often the Smart guess agrees with a hand-written answer (#2959, the owner's item 4 of
2026-10-29), on invented lore only.

    python -m dmbot.devtools.smart_guess_score [--key KEY.json]

(the key defaults to `tests/fixtures/story_graph/smart_guess/key.json`)

The upload's third choice lets the AI sort each statement of a lore read into what any inhabitant
of the world would know and what the player characters have to discover. This sends each fixture
document's statements, as the read gives them (`lore_knowledge.line`), through the same sort the
bot uses (`lore_knowledge.decide`, on the bot's own tier for lore), and scores the answer against
the key. A spoiler called world knowledge is the costly mistake (the players would be told it), so
it is counted on its own line. Counts only; the fixtures are invented, so nothing private is read.
It needs `ANTHROPIC_API_KEY`; dev1 runs it and posts the lines on #2959.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.memory import lore_knowledge

DEFAULT_KEY = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "story_graph" / "smart_guess"
    / "key.json"
)  # fmt: skip


@dataclass
class Score:
    """One document's agreement with the key, or all of them added up."""

    right_world: int = 0
    right_spoiler: int = 0
    leaked: int = 0  # a spoiler called world knowledge: the players would be told it
    held_back: int = 0  # world knowledge called a spoiler: hidden that needn't be
    # statements of a document the AI could not sort: left out of the counts above, so a failed
    # call never reads as a safe answer (the bot would keep them all as spoilers)
    unsorted: int = 0

    @property
    def total(self) -> int:
        return self.right_world + self.right_spoiler + self.leaked + self.held_back

    def add(self, other: Score) -> None:
        self.right_world += other.right_world
        self.right_spoiler += other.right_spoiler
        self.leaked += other.leaked
        self.held_back += other.held_back
        self.unsorted += other.unsorted

    def line(self, name: str) -> str:
        agree = self.right_world + self.right_spoiler
        rate = f"{100 * agree // self.total}%" if self.total else "-"
        return (
            f"{name}: agrees on {agree} of {self.total} ({rate}); spoilers called world "
            f"knowledge {self.leaked}; world knowledge called a spoiler {self.held_back}"
            + (f"; NOT SORTED (left out) {self.unsorted}" if self.unsorted else "")
        )


def lines_of(doc: Mapping[str, Any]) -> list[str]:
    """A document's statements as the lore read gives them to the sort (the same helpers)."""
    out = []
    for s in doc["statements"]:
        quote = str(s.get("quote", ""))
        if "name" in s:
            out.append(
                lore_knowledge.name_line(
                    str(s["name"]), s.get("also", ()), s.get("secret", ()), quote
                )
            )
        else:
            out.append(
                lore_knowledge.connection_line(str(s["a"]), str(s["how"]), str(s["b"]), quote)
            )
    return out


def score(doc: Mapping[str, Any], world: set[int], answered: bool) -> Score:
    """The AI's answer (`world`: the indexes it called world knowledge) against the key. A
    document the AI could not sort is counted only as not sorted."""
    if not answered:
        return Score(unsorted=len(doc["statements"]))
    got = Score()
    for i, s in enumerate(doc["statements"]):
        said, key = i in world, bool(s["world"])
        if said and key:
            got.right_world += 1
        elif not said and not key:
            got.right_spoiler += 1
        elif said:
            got.leaked += 1
        else:
            got.held_back += 1
    return got


async def run(key: Mapping[str, Any], ai: lore_knowledge.Client) -> list[str]:
    """One line per document, then the total."""
    total = Score()
    out: list[str] = []
    for doc in key["documents"]:
        world, answered = await lore_knowledge.decide(lore_knowledge.SMART, lines_of(doc), ai)
        got = score(doc, world, answered)
        total.add(got)
        out.append(got.line(str(doc["id"])))
    out.append(total.line("all"))
    return out


def _client(environ: Mapping[str, str]) -> tuple[Any, Any, str]:
    """The tier the bot's lore read sorts with, as the bot's own settings name it."""
    from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature
    from dmbot.config import parse_ai_models

    models, _ = parse_ai_models(lambda name: environ.get(name, ""))
    client = AnthropicClient(environ.get("ANTHROPIC_API_KEY", ""), models)
    feature = Feature.NAMES_BACKGROUND
    tier = FEATURE_TIERS[feature]
    return client.tier(tier, feature), client.close, str(client.model_for(tier))


async def _main(key: Mapping[str, Any]) -> int:
    from dmbot.config import ConfigError

    try:
        ai, close, model = _client(os.environ)
    except ConfigError as exc:
        print(f"smart_guess_score: {exc}", file=sys.stderr)
        return 2
    print(f"model: {model}")  # (so results compare across model changes)
    try:
        lines = await run(key, ai)
    finally:
        await close()
    for line in lines:
        print(line)
    return 1 if "NOT SORTED" in lines[-1] else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smart_guess_score", description=__doc__.split("\n")[0])
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    args = parser.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("smart_guess_score: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    key = json.loads(args.key.read_text(encoding="utf-8"))
    return asyncio.run(_main(key))


if __name__ == "__main__":
    raise SystemExit(main())
