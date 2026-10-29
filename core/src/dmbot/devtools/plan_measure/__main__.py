"""Measure what the story-plan reader costs on a text (#1714; docs/costs.md):

    python -m dmbot.devtools.plan_measure PATH                    # free: counts, estimates
    python -m dmbot.devtools.plan_measure PATH --reader anthropic --max-usd 3   # paid

PATH is a text, Markdown, Word or PDF file of an **open-licence** adventure or rules text (SRD 5.2
under CC-BY-4.0 and the like): never sourcebook or adventure text from a book, and never put such a
file in the repository. The free form reads the file as the bot does (`adventure_text`), cuts it
into chunks and batches with the bot's own code, and estimates tokens cautiously (3 characters a
token, answers at the batch limit's fifth). The paid form sends each batch to the CAREFUL tier
through the bot's own prompt and reads the answers with the bot's own checks, in memory: no
database, nothing stored. It never spends more than `--max-usd` (default $3, the per-file cap) and
stops there. The key is ANTHROPIC_API_KEY, as for the bot; it is never printed.

It prints counts and dollars only, never a name or a word of the text.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.ai import DEFAULT_MODELS, AIError, AIModelTier, AIOutOfFunds, AnthropicClient, Feature
from dmbot.devtools.plan_measure.golden import golden_text, obeyed
from dmbot.memory.adventure import FILE_MAX_BYTES, adventure_text, chunks_of
from dmbot.memory.plan_reader import MAX_TOKENS, SYSTEM, Client, book_text, unusable
from dmbot.memory.story_plan import (
    CHARS_PER_TOKEN,
    DOCUMENT_CAP_MICRO,
    ChunkRow,
    Node,
    batches_of,
    estimate_micro,
    micro_usd,
    parse_answer,
)
from dmbot.memory.story_plan import (
    tokens_in as tokens_of,
)

ANSWER_SHARE = 0.2  # a structured answer is about a fifth of what it reads (the cost decision)


def rows_of(text: str) -> list[ChunkRow]:
    return [ChunkRow(f"{n:032x}", n, c.heading, c.text) for n, c in enumerate(chunks_of(text))]


def estimate(text: str, model: str) -> dict[str, float]:
    """Counts and dollars for reading `text`, without a call."""
    batches = batches_of(rows_of(text))
    sent = [book_text([(n, r.heading, r.text) for n, r in enumerate(b, 1)], []) for b in batches]
    tokens_in = sum(tokens_of(SYSTEM + t) for t in sent)
    tokens_out = int(sum(len(t) for t in sent) / CHARS_PER_TOKEN * ANSWER_SHARE)
    return {
        "characters": len(text),
        "batches": len(batches),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "dollars": micro_usd(model, tokens_in, tokens_out) / 1_000_000,
        "dollars_worst": sum(estimate_micro(model, SYSTEM + t, MAX_TOKENS) for t in sent)
        / 1_000_000,
    }


async def measure(client: Client, text: str, model: str, max_micro: int) -> dict[str, float]:
    """Read `text` with the real prompt and checks, in memory; stops before the cap."""
    spent = calls = tokens_in = tokens_out = entries = links = unreadable = dropped = 0
    kept: list[Node] = []
    for batch in batches_of(rows_of(text)):
        numbered = [(n, r.heading, r.text) for n, r in enumerate(batch, 1)]
        body = book_text(numbered, [])
        if spent + estimate_micro(model, SYSTEM + body, MAX_TOKENS) > max_micro:
            break
        reply = await client.complete(SYSTEM, body, max_tokens=MAX_TOKENS)
        calls += 1
        tokens_in += reply.input_tokens
        tokens_out += reply.output_tokens
        spent += micro_usd(reply.model or model, reply.input_tokens, reply.output_tokens)
        parsed = parse_answer(reply.text, batch)
        entries += len(parsed.nodes)
        kept.extend(parsed.nodes)
        links += len(parsed.needs)
        dropped += sum(parsed.dropped.values())
        unreadable += 1 if unusable(reply, parsed) else 0
    return {
        "calls": calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "dollars": spent / 1_000_000,
        "entries": entries,
        "links": links,
        "dropped": dropped,
        "unreadable": unreadable,
        "obeyed": len(obeyed(kept)),
        "obeyed_uncaught": sum(1 for i in obeyed(kept) if not i.caught_by_code),
    }


def _dollars(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("needs an amount of dollars") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("needs an amount of dollars, more than 0")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dmbot.devtools.plan_measure")
    parser.add_argument(
        "path", type=Path, nargs="?", help="an open-licence text, .txt .md .docx or .pdf"
    )
    parser.add_argument(
        "--golden", action="store_true", help="read the invented book with injection lines in it"
    )
    parser.add_argument("--reader", choices=("anthropic",), help="make paid calls")
    parser.add_argument(
        "--max-usd", type=_dollars, default=DOCUMENT_CAP_MICRO / 1_000_000, help="the most to spend"
    )
    args = parser.parse_args(argv)
    model = DEFAULT_MODELS.careful
    if args.golden == (args.path is not None):
        parser.error("give a file to read, or --golden for the invented book, not both")
    if args.golden:
        text = golden_text()
    else:
        try:
            raw = args.path.read_bytes()
            if len(raw) > FILE_MAX_BYTES:
                print("That file is over 10 MB, the most the bot reads.", file=sys.stderr)
                return 2
            text = adventure_text(args.path.name, raw)
        except Exception as exc:
            print(f"Couldn't read that file ({type(exc).__name__}).", file=sys.stderr)
            return 2
    got = estimate(text, model)
    print(
        f"{got['characters']:.0f} characters in {got['batches']:.0f} batches "
        f"(model {model}, tier CAREFUL)\n"
        f"estimate, not measured: {got['tokens_in']:.0f} tokens in, {got['tokens_out']:.0f} out, "
        f"${got['dollars']:.2f} (at most ${got['dollars_worst']:.2f} if every answer ran to "
        f"{MAX_TOKENS} tokens)"
    )
    if args.reader is None:
        return 0
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("Paid calls need ANTHROPIC_API_KEY in the environment.", file=sys.stderr)
        return 2

    async def go() -> dict[str, float]:
        client = AnthropicClient(key, DEFAULT_MODELS)
        try:
            return await measure(
                client.tier(AIModelTier.CAREFUL, Feature.PLAN_READER),
                text,
                model,
                int(args.max_usd * 1_000_000),
            )
        finally:
            await client.close()

    try:
        done = asyncio.run(go())
    except (AIOutOfFunds, AIError) as exc:
        print(f"The AI service stopped it: {exc}", file=sys.stderr)
        return 1
    print(
        f"measured: {done['calls']:.0f} call(s), {done['tokens_in']:.0f} tokens in, "
        f"{done['tokens_out']:.0f} out, ${done['dollars']:.2f}; {done['entries']:.0f} entries, "
        f"{done['links']:.0f} link(s) kept, {done['dropped']:.0f} dropped by the checks, "
        f"{done['unreadable']:.0f} unreadable answer(s)"
    )
    if args.golden:
        print(
            f"injection lines obeyed (should be 0): {done['obeyed']:.0f}, of which "
            f"{done['obeyed_uncaught']:.0f} not caught by code (only the prompt kept them out)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
