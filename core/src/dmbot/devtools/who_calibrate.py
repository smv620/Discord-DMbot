"""Run the "who is he?" check on the calibration set with the real AI (#2790, slice 1).

    ANTHROPIC_API_KEY=... python -m dmbot.devtools.who_calibrate          # the cost only
    ANTHROPIC_API_KEY=... python -m dmbot.devtools.who_calibrate --yes    # the paid run

The check (`memory/who_ask.py`: one scoring call and five sample picks per passage, blended, then
the rule in `who_is_it`) only goes on in lore reads once this run meets the bar (Supervisor's ruling
on #2790): answers accepted at 80% or more ("sure") right at least 90% of the time, and answers
accepted in the signal-to-noise band ("likely") right at least 80%. Without `--yes` it calls nothing
and prints how many calls the run makes and what they would cost at the list prices on file.

The set is `tests/fixtures/who_is_it_calibration.json`: invented passages only. The output is counts
and percentages, never a passage, a name or the AI's words, so dev1 can post it on #2790 as it is.
Calls go out on the fast tier tagged `Feature.WHO_IS_IT`, as in a lore read, so they show in the
spending totals. The key comes from ANTHROPIC_API_KEY and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.claims import cost
from dmbot.devtools.common import Metered, timing_line
from dmbot.devtools.costs.model import estimate_tokens
from dmbot.devtools.costs.prices import UnknownPrice, price_of
from dmbot.memory import who_ask, who_calibration
from dmbot.memory.who_calibration import Passage, Report

DEFAULT_SET = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "who_is_it_calibration.json"
)
MAX_USD = 2.0  # the whole run; it stops starting passages once this is spent
# Output per call, for the estimate only: a scoring answer is a short list, a pick one name.
SCORE_OUT_TOKENS = 150
PICK_OUT_TOKENS = 30


def calls_per_passage(samples: int = who_ask.SAMPLES) -> int:
    return samples + 1  # one scoring call, then the sample picks


def estimate(passages: Sequence[Passage], samples: int = who_ask.SAMPLES) -> tuple[int, int, int]:
    """(calls, input tokens, output tokens) the run would use, estimated from the questions' size
    (the service counts the real numbers)."""
    calls = tokens_in = tokens_out = 0
    system = estimate_tokens(who_ask.SYSTEM)
    for p in passages:
        score_q = who_ask.question(p.text, p.phrase, p.known, who_ask.SCORES_ASK)
        tokens_in += system + estimate_tokens(score_q)
        tokens_out += SCORE_OUT_TOKENS
        for i in range(samples):
            pick_q = who_ask.question(p.text, p.phrase, p.known, who_ask.PICK_ASK, sample=i)
            tokens_in += system + estimate_tokens(pick_q)
            tokens_out += PICK_OUT_TOKENS
        calls += calls_per_passage(samples)
    return calls, tokens_in, tokens_out


def dollars(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """The list price of these tokens, or None for a model with no price on file."""
    try:
        cost_in, cost_out = price_of(model)
    except UnknownPrice:
        return None
    return cost.dollars((cost_in.dollars, cost_out.dollars), tokens_in, tokens_out)


async def run_set(
    # Any: a fake in tests, the tier client, or `Metered` around either (its `complete` takes
    # **kwargs, so no one Protocol covers all three without loosening who_ask.Client).
    client: Any,
    passages: Sequence[Passage],
    model: str,
    max_usd: float = MAX_USD,
) -> tuple[Report, int]:
    """Ask about each passage in order and tally the rule's decisions. Stops starting passages once
    `max_usd` is spent; the second value is how many were left unasked (not in the report)."""
    report = Report()
    metered = client if isinstance(client, Metered) else Metered(client)
    for done, p in enumerate(passages):
        spent = dollars(model, metered.input_tokens, metered.output_tokens) or 0.0
        if spent >= max_usd:
            return report, len(passages) - done
        asked = await who_ask.ask(metered, p.text, p.phrase, p.known)
        decision = who_ask.decide_asked(asked) if asked is not None else None
        who_calibration.tally(report, p, decision)
    return report, 0


def render(report: Report, left: int, metered: Metered | None = None, model: str = "") -> list[str]:
    """The lines to print and post: counts only."""
    out = [f"passages asked: {report.total}" + (f" ({left} not asked: the cap)" if left else "")]
    for name, (total, right) in report.counts().items():
        out.append(f"  {name}: {right} of {total}")
    out.append(f"  ai_failed: {report.ai_failed}")
    out.extend(bar.line() for bar in who_calibration.bars(report))
    if report.ai_failed:
        # A refused key or an empty account fails every call; such a run must not read as a result.
        out.append(
            f"{report.ai_failed} passages got no answer (the AI call failed): "
            "rerun before judging the bars"
        )
    if metered is not None:
        money = dollars(model, metered.input_tokens, metered.output_tokens)
        spent = "n/a (no price on file)" if money is None else f"${money:.4f}"
        out.append(
            f"calls {metered.calls}, tokens in {metered.input_tokens}, "
            f"out {metered.output_tokens}, cost {spent}"
        )
        if timing := timing_line(metered.seconds):
            out.append(f"time: {timing}")
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="who_calibrate", description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_SET)
    parser.add_argument("--yes", action="store_true", help="make the paid calls")
    parser.add_argument(
        "--max-usd",
        type=float,
        default=MAX_USD,
        help=f"stop starting passages above this (default {MAX_USD:g})",
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    # Warnings only: who_ask's own info lines carry raw scores, fine, but keep the output short.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        models = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
    except ConfigError as exc:
        print(f"who_calibrate: {exc}", file=sys.stderr)
        return 2
    passages = who_calibration.load(args.path)
    model = models.of(FEATURE_TIERS[Feature.WHO_IS_IT])
    calls, tokens_in, tokens_out = estimate(passages)
    money = dollars(model, tokens_in, tokens_out)
    if money is None:
        print(
            f"who_calibrate: no price on file for {model}: add it to "
            "dmbot.devtools.costs.prices first",
            file=sys.stderr,
        )
        return 2
    print(
        f"model {model}: {len(passages)} passages, {calls} calls, about {tokens_in} tokens in and "
        f"{tokens_out} out, about ${money:.2f} (estimate; cap ${args.max_usd:g})"
    )
    if not args.yes:
        print("No calls made: --yes runs the set (it costs money).")
        return 0
    if money > args.max_usd:
        # A run cut short by the cap can't be judged against the bars, so it isn't started.
        print(
            f"who_calibrate: the estimate is above the ${args.max_usd:g} cap; "
            "raise --max-usd to run the whole set",
            file=sys.stderr,
        )
        return 2
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("who_calibrate: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return 2
    ai = AnthropicClient(key, models)
    metered = Metered(ai.tier(FEATURE_TIERS[Feature.WHO_IS_IT], Feature.WHO_IS_IT))
    try:
        report, left = await run_set(metered, passages, model, args.max_usd)
    finally:
        await ai.close()
    print("\n".join(render(report, left, metered, model)))
    return 1 if report.ai_failed * 2 > report.total else 0  # most calls failed: not a run


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
