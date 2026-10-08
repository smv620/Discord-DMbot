"""Measure the draft claim extractor on the golden scenes (#234).

    python -m dmbot.devtools.claims                      # free: the estimate only
    python -m dmbot.devtools.claims --extractor anthropic --model ID --model ID

Paid calls happen only with --extractor anthropic, and never above --max-usd (the
estimate, an upper bound, is printed first). The key comes from ANTHROPIC_API_KEY in the
environment, as for the bot, and is never printed. --log appends the numbers to
docs/testing-history.log.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dmbot.ai import DEFAULT_MODEL, AIError, AnthropicClient
from dmbot.devtools.claims import cost
from dmbot.devtools.claims.extract import MAX_TOKENS, SYSTEM, Client, extract, transcript
from dmbot.devtools.claims.scenes import WORDS_PER_SECOND, Scene, batches, load_scenes
from dmbot.devtools.claims.score import Score, score
from dmbot.devtools.common import HISTORY, TEST_SCRIPTS, commit, history_entry, public_name

SCENES = TEST_SCRIPTS / "story-scenes.md"
BATCH_S = 30.0  # live batches are 30 to 60 s of talk
MAX_USD = 5.0  # the whole measurement, every model (Supervisor, #234)
_MODEL = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,80}$")


def _dollars(text: str) -> float:
    """A finite amount of dollars, more than nothing (NaN would pass every cap check)."""
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
    if not _MODEL.match(model):
        raise argparse.ArgumentTypeError("needs MODEL=IN,OUT, like m=3,15")
    return model, (cost_in, cost_out)


def _model(text: str) -> str:
    if not _MODEL.match(text):
        raise argparse.ArgumentTypeError("isn't a model id")
    return text


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="claims", description="Measure the draft story-memory extractor on written scenes."
    )
    parser.add_argument("--scenes", type=Path, default=SCENES, help="the golden set")
    parser.add_argument(
        "--extractor",
        choices=("none", "anthropic"),
        default="none",
        help="anthropic makes paid calls; none (the default) only estimates",
    )
    parser.add_argument(
        "--model",
        type=_model,
        action="append",
        default=[],
        help="a model to measure (repeat; default: AI_MODEL, as the bot)",
    )
    parser.add_argument(
        "--price",
        type=_price,
        action="append",
        default=[],
        help="MODEL=IN,OUT: list prices in dollars per million tokens, for a model not known",
    )
    parser.add_argument(
        "--max-usd",
        type=_dollars,
        default=MAX_USD,
        help=f"refuse above this, every model and run together (default {MAX_USD:g})",
    )
    parser.add_argument(
        "--runs",
        type=int,
        choices=range(1, 11),
        default=1,
        metavar="N",
        help="measure each model N times (1 to 10), for the spread",
    )
    parser.add_argument(
        "--batch-s",
        type=float,
        default=BATCH_S,
        help=f"join scenes into batches of at least this much talk (default {BATCH_S:g})",
    )
    parser.add_argument("--log", action="store_true", help="append to docs/testing-history.log")
    parser.add_argument("--commit", help="the commit measured (default: GIT_COMMIT, then git)")
    parser.add_argument("--history", type=Path, default=HISTORY, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def estimate(scenes: Sequence[Scene]) -> tuple[int, int]:
    """At most this many (input, output) tokens per model: input counted cautiously,
    output as if every answer ran to MAX_TOKENS."""
    input_tokens = sum(cost.tokens_for(SYSTEM + transcript(s.lines)) for s in scenes)
    return input_tokens, MAX_TOKENS * len(scenes)


def percent(part: int, whole: int) -> str:
    """78% (63-89%): the share, and its 95% range on a sample this small."""
    if not whole:
        return "n/a"
    low, high = cost.wilson(part, whole)
    return f"{part * 100 / whole:.0f}% ({low * 100:.0f}-{high * 100:.0f}%)"


@dataclass(slots=True)
class Run:
    score: Score
    usage: cost.Usage | None  # None: every call failed
    notes: list[str]
    capped: bool = False  # the cap stopped this pass part-way
    done: str = ""  # "3 of 5 batches" when it did


def record(model: str, runs: Sequence[Run], price: tuple[float, float]) -> list[str]:
    """Numbers only."""
    if not runs:
        return [f"model: {model}: not run: cap reached"]
    lines = [f"model: {model} ({len(runs)} run{'s' if len(runs) != 1 else ''}, pilot set)"]
    for n, run in enumerate(runs, 1):
        result = run.score
        kinds = ", ".join(f"{how} {right}/{of}" for how, (right, of) in result.by_kind().items())
        tag = f"  run {n}: " if len(runs) > 1 else "  "
        if run.capped:  # its numbers cover only part of the set
            tag += f"(stopped part-way: {run.done}) "
        lines += [
            f"{tag}how it was said: {result.how_right} of {result.pulled} pulled claims right "
            f"{percent(result.how_right, result.pulled)}: {kinds}",
            f"{tag}pulled: {result.pulled} of {result.expected} expected claims "
            f"{percent(result.pulled, result.expected)}; invented {result.invented}; "
            f"injections obeyed: {result.injections}",
        ]
        lines += run.notes
    used = [r.usage for r in runs if r.usage is not None]
    if not used:
        lines.append("  cost: n/a (every call failed)")
        return lines
    calls = sum(u.calls for u in used)
    tokens_in = sum(u.input_tokens for u in used)
    tokens_out = sum(u.output_tokens for u in used)
    talk_s = sum(u.talk_s for u in used)
    spent = cost.dollars(price, tokens_in, tokens_out)
    usage = cost.Usage(calls, tokens_in, tokens_out, talk_s, used[0].system_share)
    words = round(cost.SESSION_S * WORDS_PER_SECOND)
    lines += [
        f"  tokens: {tokens_in} in, {tokens_out} out over {calls} calls (about "
        f"{tokens_in // calls} in, {tokens_out // calls} out per {talk_s / calls:.0f} s "
        f"batch); cost ${spent:.3f}",
        f"  a 4-hour session, for this draft prompt, if the table talks nonstop ({words} "
        f"words): about ${usage.live(price):.2f} read live; about "
        f"${usage.after_session(price):.2f} in one pass afterwards (not measured: worked out "
        "from the batches)",
    ]
    return lines


async def measure(
    client: Client,
    scenes: Sequence[Scene],
    *,
    price: tuple[float, float] = (0.0, 0.0),
    budget_usd: float = math.inf,
) -> Run:
    """One pass over the batches. Stops before the next call once this pass has spent
    `budget_usd` or more, so the cap is overshot by one call at most. A reply that says nothing of
    its usage counts nothing against it, and is noted."""
    total = Score()
    input_tokens = output_tokens = 0
    dropped = cut = failed = calls = unbilled = 0
    talk_s = 0.0
    capped = False
    for scene in scenes:
        if cost.dollars(price, input_tokens, output_tokens) >= budget_usd:
            capped = True
            total.expected += sum(len(line.expected) for line in scene.lines)
            continue
        try:
            got = await extract(client, scene.lines)
        except AIError:
            failed += 1
            total.expected += sum(len(line.expected) for line in scene.lines)
            continue
        calls += 1
        total.add(score(scene.lines, got.claims))
        talk_s += scene.talk_s
        input_tokens += got.input_tokens
        output_tokens += got.output_tokens
        dropped += got.dropped
        cut += got.cut
        unbilled += got.input_tokens == 0 and got.output_tokens == 0
    notes = []
    if unbilled:
        replies = "reply says" if unbilled == 1 else "replies say"
        notes.append(f"  {unbilled} {replies} nothing of their usage: not counted against the cap")
    if dropped or cut or failed:
        notes.append(
            f"  answers: {dropped} claims dropped as malformed, {cut} cut off, {failed} failed "
            "(a failed call that timed out may still be billed)"
        )
    if not calls:
        return Run(total, None, notes, capped, f"{calls + failed} of {len(scenes)} batches")
    share = (
        cost.tokens_for(SYSTEM)
        / sum(cost.tokens_for(SYSTEM + transcript(s.lines)) for s in scenes)
        * len(scenes)
    )
    usage = cost.Usage(calls, input_tokens, output_tokens, talk_s, min(1.0, share))
    return Run(total, usage, notes, capped, f"{calls + failed} of {len(scenes)} batches")


async def main_async(args: argparse.Namespace) -> int:
    try:
        scenes = batches(load_scenes(args.scenes), args.batch_s)
    except (OSError, ValueError) as exc:
        print(f"claims: {exc}", file=sys.stderr)
        return 2
    models = args.model or [os.environ.get("AI_MODEL") or DEFAULT_MODEL]
    prices = {**cost.PRICES, **dict(args.price)}
    tokens_in, tokens_out = estimate(scenes)
    said = sum(len(s.lines) for s in scenes)
    wanted = sum(len(line.expected) for s in scenes for line in s.lines)
    lines = [
        f"batches: {len(scenes)} of {args.batch_s:g} s or more ({said} lines, {wanted} "
        f"expected claims), one call each per model and run; commit: "
        f"{commit(args.commit, tool='claims')}"
    ]
    total_usd = 0.0
    for model in models:
        if model not in prices:
            lines.append(f"estimate for {model}: price unknown (give --price {model}=IN,OUT)")
            continue
        usd = cost.dollars(prices[model], tokens_in, tokens_out) * args.runs
        total_usd += usd
        lines.append(
            f"estimate for {model}: about {tokens_in * args.runs} in (a guess at 3 "
            f"characters a token), at most {tokens_out * args.runs} out, ${usd:.3f}"
        )
    print("\n".join(lines))
    if args.extractor == "none":
        print("No calls made: --extractor anthropic measures (it costs money).")
        return 0
    unknown = [m for m in models if m not in prices]
    if unknown:
        print(f"claims: no price for {', '.join(unknown)}: give --price", file=sys.stderr)
        return 2
    if not total_usd <= args.max_usd:
        print(
            f"claims: the estimate, ${total_usd:.2f}, is over ${args.max_usd:g}; "
            "--max-usd raises it",
            file=sys.stderr,
        )
        return 2
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("claims: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return 2
    out = lines[:1]
    spent = 0.0
    measured = capped = False
    for model in models:
        runs: list[Run] = []
        if not capped:
            client = AnthropicClient(key, model)
            try:
                for _ in range(args.runs):
                    if spent > args.max_usd:  # the estimate was wrong: stop, don't overspend
                        out.append(f"stopped: ${spent:.2f} spent, over ${args.max_usd:g}")
                        capped = True
                        break
                    run = await measure(
                        client, scenes, price=prices[model], budget_usd=args.max_usd - spent
                    )
                    runs.append(run)
                    if run.usage is not None:
                        measured = True
                        spent += cost.dollars(
                            prices[model], run.usage.input_tokens, run.usage.output_tokens
                        )
                    if run.capped:
                        out.append(f"stopped part-way: ${spent:.2f} spent, cap ${args.max_usd:g}")
                        capped = True
                        break
            finally:
                await client.close()
        out += record(model, runs, prices[model])
    print("\n".join(out[1:]))
    if args.log and measured:
        with args.history.open("a", encoding="utf-8") as history:
            name = public_name(args.scenes, other="a scenes file")
            title = f"story-memory extraction, {name}"
            history.write(history_entry(out, title=title, kind="Measurement"))
        print(f"\nAppended to {args.history}")
    elif args.log:
        print("\nNothing logged: every call failed.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
