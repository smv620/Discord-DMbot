"""Run the sidebar's table questions against the real model and print the answers (#934).

CI runs the same questions with a fake AI (`tests/test_sidebar_answer.py`); this is the one
check with the real thing, run by hand on the server before the sidebar goes live, so
Supervisor can read the answers and the times:

    ANTHROPIC_API_KEY=... python -m dmbot.devtools.sidebar_check [path/to/other_cases.py]
        [--fast-model MODEL]   # the fast tier's model for this run only (#2055)

It prints each question, DMbot's answer and how long it took, then whether the answer kept to
the length rules. The key is read from the environment and never printed. It uses a made-up
campaign (one sample house rule, no names) and spends a few cents of the operator's AI money.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from dmbot.ai import FEATURE_TIERS, AIError, AnthropicClient, Feature
from dmbot.campaigns.models import Campaign
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools import sidebar_cases
from dmbot.devtools.common import add_fast_model_arg, timing_line, with_fast_model
from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.sidebar import brevity
from dmbot.sidebar.answer import Sidebar

# The questions ship in the package (`dmbot.devtools.sidebar_cases`), so the tool finds them
# wherever DMbot is installed, from any folder. A path given on the command line still wins.
DEFAULT_CASES = Path(sidebar_cases.__file__)
MAX_ERRORS_IN_A_ROW = 3
SAMPLE_HOUSE_RULE = HouseRule(
    3, "check", "Criticals deal maximum damage plus the roll.", "Critical hits", None, None, 1, 0, 0
)


def load_cases(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("sidebar_brevity_cases", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Can't read {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_campaign() -> Campaign:
    return Campaign(
        id="check",
        guild_id=1,
        name="Sample",
        created_at=0,
        last_played_at=None,
        target_ruleset="2024",
        fallback_ruleset="2014",
        optional_rules_default=True,
        dm_user_ids=frozenset({1}),
        dm_screen_channel_id=None,
        last_voice_channel_id=None,
        dm_screen_visibility="peek",
        owner_user_id=1,
    )


@dataclass(frozen=True, slots=True)
class Checked:
    """What the cases came to: how many failed, and the lines printed for each."""

    failed: int
    total: int
    lines: list[str]
    errors: list[str] = field(default_factory=list)  # the AI errors' own plain words, each once
    seconds: list[float] = field(default_factory=list)  # each answered question's time (#2263)


async def check_cases(sidebar: Sidebar, cases: Sequence[Any], *, quiet: bool = False) -> Checked:
    """Ask each case's question and judge the answer by the length rules and the case's own
    must-say and must-not-say words. `quiet`: collect the lines without printing them."""
    failed = 0
    lines: list[str] = []
    errors: list[str] = []
    seconds: list[float] = []
    in_a_row = 0

    def say(text: str) -> None:
        lines.append(text)
        if not quiet:
            print(text)

    for number, case in enumerate(cases, start=1):
        started = time.monotonic()
        try:
            got = await sidebar.answer(sample_campaign(), case.question, asker_id=1)
        except AIError as exc:  # the AI service said no: that case fails, with its words
            failed += 1
            in_a_row += 1
            if str(exc) not in errors:
                errors.append(str(exc))
            say(f"{number:2}. {case.question}")
            say(f"    → AI ERROR: {exc}")
            if in_a_row >= MAX_ERRORS_IN_A_ROW:  # a refused model or key would only fail again
                say(f"    stopped after {in_a_row} errors in a row; the rest were not asked")
                break
            continue
        in_a_row = 0
        took = time.monotonic() - started
        seconds.append(took)
        body = got.text.rsplit(" (", 1)[0] if got.text.endswith(")") else got.text
        ok = brevity.within_limit(body) or len(got.parts) > 1
        # What the answer must and must not say (#992): the words are the case's own.
        lowered = got.text.lower()
        missing = bool(case.must_any) and not any(w.lower() in lowered for w in case.must_any)
        banned = [w for w in case.must_not if w.lower() in lowered]
        failed += not ok or missing or bool(banned)
        verdict = "ok" if ok else "TOO LONG"
        if missing:
            verdict += f", MISSING one of {list(case.must_any)}"
        if banned:
            verdict += f", MUST NOT SAY {banned}"
        say(f"{number:2}. {case.question}")
        say(f"    → {got.text}")
        say(f"    {took:.1f} s, {len(got.text)} characters, {verdict}, answered by {got.model}")
    return Checked(failed, len(cases), lines, errors, seconds)


def new_sidebar(client: Any) -> Sidebar:
    """The sidebar on a made-up campaign (one sample house rule, no names)."""

    async def gate(campaign: Campaign, user: int) -> str | None:
        return None

    async def houses(campaign: Campaign) -> Sequence[HouseRule]:
        return [SAMPLE_HOUSE_RULE]

    async def names(campaign: Campaign) -> None:
        return None

    return Sidebar(client, srd(), gate=gate, houses=houses, names=names)


async def run(path: Path, fast_model: str | None = None) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise SystemExit("Set ANTHROPIC_API_KEY (the server's key) to run this.")
    cases = load_cases(path).CASES
    try:
        models, _ = parse_ai_models(lambda name: os.environ.get(name, ""))
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    ai_client = AnthropicClient(key, with_fast_model(models, fast_model))
    client = ai_client.tier(FEATURE_TIERS[Feature.SIDEBAR])
    print(f"Model: {client.model} (tier {client.tier.value})\n")
    try:
        result = await check_cases(new_sidebar(client), cases)
    finally:
        await ai_client.close()
    passed = result.total - result.failed
    print(f"\n{passed} of {result.total} passed (length, must-say and must-not-say).")
    print(f"Time per answer: {timing_line(result.seconds)}.")
    return 1 if result.failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sidebar_check", description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CASES)
    add_fast_model_arg(parser)
    args = parser.parse_args(argv)
    return asyncio.run(run(args.path, args.fast_model))


if __name__ == "__main__":
    raise SystemExit(main())
