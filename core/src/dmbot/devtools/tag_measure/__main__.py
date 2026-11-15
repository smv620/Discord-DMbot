"""Measure the session tagger on the sample session (#4456; `docs/design/session_tagger.md` §10
and §14 slice 1). dev1 runs it after a deploy that has it:

    python -m dmbot.devtools.tag_measure SESSION_FILE                 # free: counts, an estimate
    python -m dmbot.devtools.tag_measure SESSION_FILE --reader anthropic --max-usd 2   # paid

SESSION_FILE is the sample session's plain-text export (the public sample,
`core/tests/fixtures/story_graph/README.md`), read as the bot reads an imported session: cut into
batches of lines with the extractor's own code. The paid form sends each batch to the fast model
through the tagger's prompt (`memory/tagger.py`), with the two lines before it for context, and
checks each answer with the tagger's own parser, in memory: no database, nothing stored. It never
spends more than `--max-usd` and stops there. The key is ANTHROPIC_API_KEY, as for the bot; it is
never printed.

The batches are the extractor's, with each line's own time, so lines said in the same moment stay
together, as the bot does. Unlike the bot's, they hold everything said: the off-topic filter (an AI
pass) and the "Previously" recap are not taken out, so read the numbers as batches of all talk.

It prints, for each tag: the session key's rows of that kind and how many fall in a batch carrying
the tag (reach), and with a neighbour batch too; the share of batches tagged (load); how many of
those were only "maybe"; then the calls, failed batches, tokens and dollars. Counts only, never a
name or a word of the session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dmbot.ai import (
    DEFAULT_MODELS,
    OUTPUT_FLOOR,
    AIError,
    AIModelTier,
    AIOutOfFunds,
    AnthropicClient,
    Feature,
)
from dmbot.devtools.tag_measure import score
from dmbot.memory import extractor, tagger
from dmbot.memory.story_plan import CHARS_PER_TOKEN, micro_usd
from dmbot.transcript import importer

KEY = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "story_graph" / "session_key.json"
)
ANSWER_TOKENS = 120  # a tagging answer is a short list (an estimate, before measuring)


def lines_of(text: str) -> tuple[list[tagger.Line], list[int], int]:
    """The session's lines as the tagger is shown them, the file line each starts on, and how
    many couldn't be placed in the file."""
    said = importer.parse(text).utterances
    lines = [tagger.Line(i, u.speaker, u.text) for i, u in enumerate(said, 1)]
    starts = score.file_lines(text, [u.text for u in said])
    unplaced = sum(1 for i in range(1, len(starts)) if starts[i] == starts[i - 1])
    return lines, starts, unplaced


def times_of(text: str) -> list[int]:
    """Each line's time as the importer gives it (a plain file with none gets one a line), so the
    extractor's rule keeps lines said in the same moment in one batch (the reviewer on #4456)."""
    return [u.at_ms for u in importer.parse(text).utterances]


def batches_of(
    lines: Sequence[tagger.Line], times: Sequence[int] | None = None
) -> list[list[tagger.Line]]:
    """The extractor's own batches (`extractor.batches`), each line at its own time (`times`; one
    a line when none is given)."""
    at = list(times) if times is not None else [x.number for x in lines]
    said = [extractor.Said(x.number, 0, x.text, started_ms=at[i]) for i, x in enumerate(lines)]
    by_number = {x.number: x for x in lines}
    return [[by_number[s.number] for s in group] for group in extractor.batches(said)]


def estimate(batches: Sequence[Sequence[tagger.Line]], model: str) -> dict[str, float]:
    sent = [
        tagger.INSTRUCTIONS + tagger.batch_text(b, _before(batches, i))
        for i, b in enumerate(batches)
    ]
    tokens_in = int(sum(len(t) for t in sent) / CHARS_PER_TOKEN)
    tokens_out = ANSWER_TOKENS * len(batches)
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "dollars": micro_usd(model, tokens_in, tokens_out) / 1_000_000,
    }


def _before(batches: Sequence[Sequence[tagger.Line]], i: int) -> list[tagger.Line]:
    return list(batches[i - 1][-tagger.CONTEXT_LINES :]) if i > 0 else []


async def measure(
    ai: Any, batches: Sequence[Sequence[tagger.Line]], model: str, max_micro: int
) -> tuple[list[tagger.Tagging], int]:
    """Tag each batch, in order, until the cap. What came of each, and the millionths spent."""
    done: list[tagger.Tagging] = []
    spent = 0
    for i, batch in enumerate(batches):
        body = tagger.INSTRUCTIONS + tagger.batch_text(batch, _before(batches, i))
        # (two calls at most, each up to the floor's answer: the retry's counts too)
        worst = micro_usd(model, int(len(body) / CHARS_PER_TOKEN) * 2, 2 * OUTPUT_FLOOR)
        if spent + worst > max_micro:
            break
        try:
            got = await tagger.tag_batch(ai, batch, _before(batches, i), max_tokens=OUTPUT_FLOOR)
        except (AIOutOfFunds, AIError) as exc:  # (stop here; what was done is still reported)
            print(f"The AI service stopped it at batch {i + 1}: {type(exc).__name__}")
            break
        spent += micro_usd(
            got.model or model, got.input_tokens, got.output_tokens, got.cache_read_tokens,
            got.cache_write_tokens,
        )  # fmt: skip
        done.append(got)
    return done, spent


def _dollars(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("needs an amount of dollars") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("needs an amount of dollars, more than 0")
    return number


def print_report(
    table: dict[str, dict[str, float]], batches: int, done: Sequence[tagger.Tagging], spent: int
) -> None:
    print(f"{'tag':8} {'rows':>4} {'reach':>9} {'+near':>9} {'load':>9} {'maybe':>5}")
    for key, r in table.items():
        rows = int(r["rows"])
        reach = f"{int(r['reach'])}/{rows}" if rows else "-"
        near = f"{int(r['reach_near'])}/{rows}" if rows else "-"
        load = f"{int(r['tagged'])} {r['load']:.0%}"
        print(f"{key:8} {rows:>4} {reach:>9} {near:>9} {load:>9} {int(r['maybe']):>5}")
    calls = sum(t.calls for t in done)
    failed = sum(1 for t in done if t.status == tagger.FAILED)
    dropped: Counter[str] = Counter()
    for t in done:
        dropped.update(t.dropped)
    print(
        f"{len(done)} of {batches} batches tagged, {calls} call(s), {failed} failed; "
        f"{sum(t.input_tokens for t in done)} tokens in, "
        f"{sum(t.output_tokens for t in done)} out, "
        f"{sum(t.cache_read_tokens for t in done)} read from the cache, "
        f"{sum(t.cache_write_tokens for t in done)} written to it; ${spent / 1_000_000:.2f}"
    )
    said = ", ".join(f"{n} {why}" for why, n in sorted(dropped.items())) or "none"
    print(f"dropped by the checks: {said}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dmbot.devtools.tag_measure")
    parser.add_argument("session", type=Path, help="the sample session's plain-text export")
    parser.add_argument("--key", type=Path, default=KEY, help="the session key (JSON)")
    parser.add_argument("--reader", choices=("anthropic",), help="make paid calls")
    parser.add_argument("--max-usd", type=_dollars, default=2.0, help="the most to spend")
    args = parser.parse_args(argv)
    model = DEFAULT_MODELS.fast
    try:
        text = args.session.read_text(encoding="utf-8")
        key = json.loads(args.key.read_text(encoding="utf-8"))
        lines, starts, unplaced = lines_of(text)
    except Exception as exc:  # the type only: a parse error can quote the file
        print(f"Couldn't read those files ({type(exc).__name__}).", file=sys.stderr)
        return 2
    batches = batches_of(lines, times_of(text))
    spans = score.spans([[x.number for x in b] for b in batches], starts, text.count("\n") + 1)
    rows = score.key_rows(key)
    got = estimate(batches, model)
    print(
        f"{len(lines)} lines in {len(batches)} batches of all talk ({unplaced} not placed in the "
        f"file); {len(rows)} key rows to reach, {score.outside(rows, spans)} outside the "
        f"session's lines (model {model}, tier FAST)\n"
        f"estimate, not measured: {got['tokens_in']:.0f} tokens in, {got['tokens_out']:.0f} out, "
        f"${got['dollars']:.2f}"
    )
    if args.reader is None:
        return 0
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Paid calls need ANTHROPIC_API_KEY in the environment.", file=sys.stderr)
        return 2

    async def go() -> tuple[list[tagger.Tagging], int]:
        client = AnthropicClient(api_key, DEFAULT_MODELS)
        try:
            ai = client.tier(AIModelTier.FAST, Feature.EXTRACTOR, cache_system=True)
            return await measure(ai, batches, model, int(args.max_usd * 1_000_000))
        finally:
            await client.close()

    done, spent = asyncio.run(go())
    table = score.report(
        spans[: len(done)],
        [t.tags for t in done],
        [t.status == tagger.FAILED for t in done],
        score.measured_rows(rows, spans, len(done)),
    )
    print_report(table, len(batches), done, spent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
