"""Compare two fast models on DMbot's own golden sets (#2055). It measures; it switches nothing.

    ANTHROPIC_API_KEY=... python -m dmbot.devtools.fast_compare --yes

Runs the story extractor set (`story_measure`), the recap writer set (`recap_measure`) and the
sidebar's table questions (`sidebar_check`), each once with the bot's own fast model and once with
the candidate (`--candidate`, default `dmbot.ai.FAST_CANDIDATE`), and prints one table for each
tool: its own quality figure, passes and fails, the tokens in and out, and the dollars at the
list prices on file (`devtools/costs/prices.py`). Without `--yes` nothing is called and it only
says what it would run. Counts and the tools' own figures only, on the invented golden data, never
live data. The key comes from ANTHROPIC_API_KEY and is never printed. The sidebar's questions are
and the golden sets ship inside the package (`dmbot/devtools/golden`, `sidebar_cases.py`), so it
runs from any folder where DMbot is installed. It prints each call's time, with the median and
the slowest, for each model.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.ai import FAST_CANDIDATE, PREVIOUS_FAST, AIError, AIModels, AIModelTier, AnthropicClient
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools import sidebar_check
from dmbot.devtools.claims import cost
from dmbot.devtools.common import Metered, timing_line
from dmbot.devtools.costs.prices import UnknownPrice, price_of
from dmbot.devtools.recap_measure import __main__ as recap_measure
from dmbot.devtools.recap_measure.golden import GoldenError as RecapGoldenError
from dmbot.devtools.recap_measure.golden import load_golden as load_recap
from dmbot.devtools.story_measure import __main__ as story_measure
from dmbot.devtools.story_measure.golden import GoldenError as StoryGoldenError
from dmbot.devtools.story_measure.golden import load_golden as load_story

PAUSE_S = 20.0  # between runs
MAX_USD = 5.0  # the whole comparison, every tool and both models
TOOLS = ("story", "recap", "sidebar")

# What stops one run without stopping the comparison: the service, the files, a bad golden set.
CAUGHT: tuple[type[BaseException], ...] = (AIError, OSError, StoryGoldenError, RecapGoldenError)
MakeClient = Callable[[str], Awaitable[tuple[Any, Callable[[], Awaitable[None]]]]]


@dataclass(slots=True)
class Row:
    """One model's result on one tool."""

    model: str
    quality: str = ""
    passed: int = 0
    failed: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    dollars: float | None = None  # None: no price on file for the model
    note: str = ""
    seconds: list[float] = field(default_factory=list)  # each call's time, in order (#2263)


@dataclass(slots=True)
class Table:
    tool: str
    rows: list[Row] = field(default_factory=list)


def dollars_for(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """The list price of these tokens, or None for a model with no price on file."""
    try:
        cost_in, cost_out = price_of(model)
    except UnknownPrice:
        return None
    return cost.dollars((cost_in.dollars, cost_out.dollars), tokens_in, tokens_out)


async def run_story(client: Metered, model: str, budget: float = MAX_USD) -> Row:
    scenes = load_story()
    price = story_measure.price_for(model, {}) or (0.0, 0.0)  # (main_async refuses unpriced)
    got = await story_measure.measure(client, scenes, price, budget)
    score = got.score
    return Row(
        model,
        quality=f"pulled {score.pulled}/{score.expected} claims, invented {score.invented}",
        passed=score.pulled,
        failed=score.expected - score.pulled,
        note="; ".join(n.strip() for n in got.notes),
    )


async def run_recap(client: Metered, model: str, budget: float = MAX_USD) -> Row:
    total = recap_measure.Score()
    clean = flawed = 0
    note = ""
    for session in load_recap():
        try:
            one = await recap_measure.run_one(client, session)
        except AIError as exc:
            note = f"stopped: a call failed ({type(exc).__name__})"
            break
        recap_measure.add(total, one)
        bad = one.invented or one.quote_leaks or one.secret_leaks
        clean, flawed = clean + (not bad), flawed + bool(bad)
    return Row(
        model,
        quality=(
            f"beat recall {total.found}/{total.reference} ({total.recall:.0%}), invented "
            f"{total.invented}, quote leaks {total.quote_leaks}, secret leaks {total.secret_leaks}"
        ),
        passed=clean,  # sessions with none of the three that must be 0
        failed=flawed,
        note=note,
    )


async def run_sidebar(client: Metered, model: str, budget: float = MAX_USD) -> Row:
    cases = sidebar_check.load_cases(sidebar_check.DEFAULT_CASES).CASES
    done = await sidebar_check.check_cases(sidebar_check.new_sidebar(client), cases, quiet=True)
    return Row(
        model,
        quality=f"{done.total - done.failed} of {done.total} questions kept to the rules",
        passed=done.total - done.failed,
        failed=done.failed,
        note="; ".join(f"AI error: {e}" for e in done.errors),  # the service's own plain words
    )


RUNNERS: dict[str, Callable[..., Awaitable[Row]]] = {
    "story": run_story,
    "recap": run_recap,
    "sidebar": run_sidebar,
}


async def compare(
    models: Sequence[str],
    tools: Sequence[str],
    make_client: MakeClient,
    *,
    max_usd: float = MAX_USD,
    pause: float = 0.0,
) -> list[Table]:
    """Each tool on each model, in order. Stops starting new runs once `max_usd` is spent.
    `pause`: seconds to wait between runs, so the service's per-minute limits are not what is
    measured (a run right after another may be told to wait)."""
    tables: list[Table] = []
    spent = 0.0
    first = True
    for tool in tools:
        table = Table(tool)
        tables.append(table)
        for model in models:
            if spent >= max_usd:
                table.rows.append(Row(model, note=f"not run: the ${max_usd:g} cap was reached"))
                continue
            if pause and not first:
                await asyncio.sleep(pause)
            first = False
            raw, close = await make_client(model)
            metered = Metered(raw)
            try:
                row = await RUNNERS[tool](metered, model, max_usd - spent)
            except CAUGHT as exc:
                # (an AI error is written in plain words for people and holds no key; the others
                # are named by type only, as a file path could be in their text)
                said = f": {exc}" if isinstance(exc, AIError) else ""
                row = Row(model, note=f"stopped: {type(exc).__name__}{said}")
            finally:
                await close()
            row.tokens_in, row.tokens_out = metered.input_tokens, metered.output_tokens
            row.dollars = dollars_for(model, row.tokens_in, row.tokens_out)
            row.seconds = list(metered.seconds)
            spent += row.dollars or 0.0
            table.rows.append(row)
    return tables


def render(tables: Sequence[Table]) -> list[str]:
    """One table for each tool: model, quality, passed/failed, tokens in/out, dollars."""
    out: list[str] = []
    for table in tables:
        out += ["", f"{table.tool}:"]
        for row in table.rows:
            money = "n/a (no price on file)" if row.dollars is None else f"${row.dollars:.4f}"
            out.append(
                f"  {row.model}: {row.quality or '-'}; passed {row.passed}, failed {row.failed}; "
                f"tokens {row.tokens_in} in / {row.tokens_out} out; {money}"
            )
            if row.seconds:
                each = " ".join(f"{t:.1f}" for t in row.seconds)
                out.append(f"    time per call: {timing_line(row.seconds)}")
                out.append(f"    each (s): {each}")
            if row.note:
                out.append(f"    note: {row.note}")
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fast_compare", description=__doc__)
    parser.add_argument(
        "--candidate",
        default=FAST_CANDIDATE,
        help=f"the model to try (default {FAST_CANDIDATE})",
    )
    parser.add_argument(
        "--baseline",
        default=PREVIOUS_FAST,
        help=f"the model to measure it against (default {PREVIOUS_FAST}, the tier before #2152)",
    )
    parser.add_argument(
        "--tool", action="append", choices=TOOLS, default=[], help="a tool to run (default: all)"
    )
    parser.add_argument("--yes", action="store_true", help="make the paid calls")
    parser.add_argument(
        "--pause",
        type=float,
        default=PAUSE_S,
        help=f"seconds to wait between runs (default {PAUSE_S:g}), against the per-minute limits",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=MAX_USD,
        help=f"stop starting runs above this (default {MAX_USD:g}); the story run also stops "
        "inside itself, the recap and sidebar runs only between runs, so it is approximate",
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    # The client logs why a call failed (the status and the service's reason; never the key), so
    # a run that stops says why. Warnings and errors only.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        base = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
    except ConfigError as exc:
        print(f"fast_compare: {exc}", file=sys.stderr)
        return 2
    models = [args.baseline, args.candidate]
    tools = args.tool or list(TOOLS)
    print(
        f"fast tier now: {base.fast}; baseline: {args.baseline}; candidate: {args.candidate}; "
        f"tools: {', '.join(tools)}"
    )
    if unpriced := [m for m in models if dollars_for(m, 1, 1) is None]:
        print(
            f"fast_compare: no price on file for {', '.join(unpriced)}: add it to "
            "dmbot.devtools.costs.prices first",
            file=sys.stderr,
        )
        return 2
    if not args.yes:
        print("No calls made: --yes runs the golden sets on both models (it costs money).")
        return 0
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("fast_compare: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return 2

    async def make_client(model: str) -> tuple[Any, Callable[[], Awaitable[None]]]:
        ai = AnthropicClient(key, AIModels.same(model))  # one model: no quiet fall back
        return ai.tier(AIModelTier.FAST), ai.close

    tables = await compare(models, tools, make_client, max_usd=args.max_usd, pause=args.pause)
    print("\n".join(render(tables)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
