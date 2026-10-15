"""Measure a candidate story-memory extractor on the invented golden set (#1268).

    python -m dmbot.devtools.story_measure                    # free: checks the set, estimates
    python -m dmbot.devtools.story_measure --extractor anthropic --tier fast --tier careful

Paid calls happen only with `--extractor anthropic`, never above `--max-usd` (the estimate, an
upper bound, is printed first), and never in CI: CI runs the format check on the set and the
scoring on canned model outputs. The key comes from ANTHROPIC_API_KEY in the environment, as for
the bot, and is never printed. The golden set is core/tests/golden/story (see `golden.py`).

Each scene is one call: the draft extractor prompt of `dmbot.devtools.claims.extract` (fixed JSON
answer, the lines sent as quoted data), so what is measured is what the live extractor would do.
The tiers are the bot's own (`AI_MODEL_FAST`, `AI_MODEL_CAREFUL`, `AI_MODEL_DEEP`); STRONG in
docs/costs.md means CAREFUL. Every call names one model: a refused model is an error here, never
a quiet fall back to a cheaper one, so a number is always for the model that is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from dmbot.ai import AIError, AIModels, AIModelTier, AnthropicClient
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.claims import cost
from dmbot.devtools.claims.extract import MAX_TOKENS, SYSTEM, Client, extract, transcript
from dmbot.devtools.claims.scenes import Scene
from dmbot.devtools.claims.score import Score, score
from dmbot.devtools.costs.prices import UnknownPrice, price_of
from dmbot.devtools.story_measure import report
from dmbot.devtools.story_measure.golden import GOLDEN, GoldenError, load_golden, summary

MAX_USD = 5.0  # the whole measurement, every tier together
TIERS = {"fast": AIModelTier.FAST, "careful": AIModelTier.CAREFUL, "deep": AIModelTier.DEEP}


def _dollars(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("needs an amount of dollars") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("needs an amount of dollars, more than 0")
    return value


def _price(text: str) -> tuple[str, tuple[float, float]]:
    """MODEL=IN,OUT in dollars per million tokens."""
    model, _, prices = text.partition("=")
    try:
        cost_in, cost_out = (_dollars(p) for p in prices.split(","))
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError("needs MODEL=IN,OUT, more than 0, like m=3,15") from None
    if not model:
        raise argparse.ArgumentTypeError("needs MODEL=IN,OUT, like m=3,15")
    return model, (cost_in, cost_out)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="story_measure", description="Measure the draft story extractor on the golden set."
    )
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="the folder of scenes")
    parser.add_argument(
        "--extractor",
        choices=("none", "anthropic"),
        default="none",
        help="anthropic makes paid calls; none (the default) only checks and estimates",
    )
    parser.add_argument(
        "--tier",
        choices=sorted(TIERS),
        action="append",
        default=[],
        help="a tier to measure (repeat; default: fast)",
    )
    parser.add_argument(
        "--price",
        type=_price,
        action="append",
        default=[],
        help="MODEL=IN,OUT: list prices in dollars per million tokens, for a model not on file",
    )
    parser.add_argument(
        "--max-usd",
        type=_dollars,
        default=MAX_USD,
        help=f"refuse above this, every tier together (default {MAX_USD:g})",
    )
    return parser.parse_args(argv)


def price_for(model: str, given: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Dollars per million tokens (input, output): one given with --price, else the checked list
    price on file (dmbot.devtools.costs.prices), else None: a price is never guessed."""
    if model in given:
        return given[model]
    try:
        cost_in, cost_out = price_of(model)
    except UnknownPrice:
        return None
    return cost_in.dollars, cost_out.dollars


CLAIM_TOKENS = 45  # one claim in the answer's JSON, roughly: an estimate until measured


def estimate(scenes: Sequence[Scene], *, worst: bool) -> list[report.Call]:
    """Each call's tokens before any call is made: the input counted cautiously (3 characters
    a token). The output is a guess from the claims the set expects, or, `worst`, as if every
    answer ran to the limit (that one decides the cap)."""
    return [
        report.Call(
            cost.tokens_for(SYSTEM + transcript(s.lines)),
            MAX_TOKENS
            if worst
            else 20 + CLAIM_TOKENS * sum(len(line.expected) for line in s.lines),
        )
        for s in scenes
    ]


async def measure(
    client: Client, scenes: Sequence[Scene], price: tuple[float, float], budget_usd: float
) -> tuple[Score, list[report.Call], list[str]]:
    """One pass over the scenes. Stops before the next call once `budget_usd` is spent (so
    the cap is overshot by one call at most). Returns the score, each call's tokens and notes."""
    total = Score()
    calls: list[report.Call] = []
    notes: list[str] = []
    failed = dropped = cut = 0
    spent = 0.0
    for scene in scenes:
        if spent >= budget_usd:
            notes.append(f"  stopped at ${spent:.2f}: the cap; later scenes not measured")
            break
        try:
            got = await extract(client, scene.lines)
        except AIError:
            failed += 1
            total.expected += sum(len(line.expected) for line in scene.lines)
            continue
        total.add(score(scene.lines, got.claims))
        calls.append(report.Call(got.input_tokens, got.output_tokens))
        spent += cost.dollars(price, got.input_tokens, got.output_tokens)
        dropped += got.dropped
        cut += got.cut
    if dropped or cut or failed:
        notes.append(
            f"  answers: {dropped} claims dropped as malformed, {cut} cut off, {failed} calls "
            "failed (a failed call that timed out may still be billed)"
        )
    return total, calls, notes


async def main_async(args: argparse.Namespace) -> int:
    try:
        scenes = load_golden(args.golden)
    except (OSError, GoldenError) as exc:
        print(f"story_measure: {exc}", file=sys.stderr)
        return 2
    try:
        models = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
    except ConfigError as exc:
        print(f"story_measure: {exc}", file=sys.stderr)
        return 2
    tiers = args.tier or ["fast"]
    given = dict(args.price)
    out = [f"golden set: {summary(scenes)}", f"date: {date.today().isoformat()}"]
    worst, guess = estimate(scenes, worst=True), estimate(scenes, worst=False)
    chosen: list[tuple[str, AIModelTier, str, tuple[float, float] | None]] = []
    total_usd = 0.0
    for name in tiers:
        tier = TIERS[name]
        model = models.of(tier)
        price = price_for(model, given)
        chosen.append((name, tier, model, price))
        if price is None:
            out.append(
                f"estimate for {name} ({model}): price unknown (give --price {model}=IN,OUT)"
            )
            continue
        usd = cost.dollars(
            price, sum(c.input_tokens for c in worst), sum(c.output_tokens for c in worst)
        )
        total_usd += usd
        out += [
            f"estimate for {name} ({model}), not measured (answer size guessed from the claims "
            "the set expects):",
            *report.token_lines(guess, price),
            f"  the whole measurement: at most ${usd:.3f} (every answer at the limit)",
        ]
    print("\n".join(out))
    if args.extractor == "none":
        print("No calls made: --extractor anthropic measures (it costs money).")
        return 0
    if unknown := [m for _, _, m, p in chosen if p is None]:
        print(f"story_measure: no price for {', '.join(unknown)}: give --price", file=sys.stderr)
        return 2
    if not total_usd <= args.max_usd:
        print(
            f"story_measure: the estimate, ${total_usd:.2f}, is over ${args.max_usd:g}; "
            "--max-usd raises it",
            file=sys.stderr,
        )
        return 2
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("story_measure: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return 2
    spent = 0.0
    results = []
    for name, tier, model, price in chosen:
        assert price is not None
        ai = AnthropicClient(key, AIModels.same(model))  # one model: no quiet fall back
        try:
            result, calls, notes = await measure(ai.tier(tier), scenes, price, args.max_usd - spent)
        finally:
            await ai.close()
        spent += cost.dollars(
            price, sum(c.input_tokens for c in calls), sum(c.output_tokens for c in calls)
        )
        results += [
            f"measured {name} ({model}):",
            *report.score_lines(result),
            *report.token_lines(calls, price),
            *notes,
        ]
    print("\n".join(results))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
