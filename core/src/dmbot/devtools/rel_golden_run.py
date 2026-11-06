"""Scores relationship typing on the golden set with the real embeddings, counts only (#3244).

    VOYAGE_API_KEY=... python -m dmbot.devtools.rel_golden_run [--threshold 0.6] [--margin 0.03]

For each golden case (`memory.rel_golden`, invented sentences) it finds the closest relationship
type by meaning (`memory.rel_types`: a type's text is what it *is* plus its examples, never its
`not_this`), then reports, with no sentence printed, only numbers:

- how many cases are right, wrong and held back (a weak match, where the reader's own pick would
  stand) at the given threshold and margin, and the precision of the matches that get used;
- `choose`: the loosest threshold and margin whose used matches reach the owner's 80% precision,
  with how many cases they cover (the pair to set before the matching is switched on), or none;
- which type each wrong match landed on, as counts of `expected -> got`;
- for the cases that must land on no tie (`none`) or on another name (`alias`): how many a
  used match would wrongly turn into a stored tie.

The key is read from the environment and never printed. Descriptions are embedded as queries and
the type texts as documents. Nothing is written to a database. The pure part (`evaluate`) takes
the embedder as an argument, so it is tested without the network.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from dmbot.memory import rel_golden, rel_types
from dmbot.memory.rel_type_texts import BY_KEY, TYPES

Embed = Callable[
    [Sequence[str], bool], Awaitable[list[list[float]]]
]  # (texts, as_query) -> vectors


@dataclass(slots=True)
class Result:
    threshold: float
    margin: float
    used: int = 0
    right: int = 0
    wrong: int = 0
    held: int = 0
    stored_wrongly: int = 0  # a `none` or `alias` case that a used match would store as a tie
    confusion: Counter[str] = field(default_factory=Counter)
    chosen: rel_types.Choice | None = None
    cases: int = 0


async def evaluate(
    embed: Embed,
    *,
    threshold: float = rel_types.THRESHOLD,
    margin: float = rel_types.MARGIN,
    cases: Sequence[rel_golden.Case] = rel_golden.CASES,
) -> Result:
    """Match every case and count. `embed(texts, as_query)` returns one vector per text."""
    type_vectors = await embed([rel_types.embed_text(t) for t in TYPES], False)
    vectors = {t.key: v for t, v in zip(TYPES, type_vectors, strict=True)}
    case_vectors = await embed([c.description for c in cases], True)
    result = Result(threshold, margin, cases=len(cases))
    scored: list[rel_types.Scored] = []
    for case, vec in zip(cases, case_vectors, strict=True):
        match = rel_types.nearest(vec, vectors)
        if match is None:
            result.held += 1
            continue
        decision = rel_types.decide(case.expected, match, threshold=threshold, margin=margin)
        if case.expected in BY_KEY:
            scored.append(
                rel_types.Scored(match.key, match.score, match.second_score, case.expected)
            )
        if decision.source != rel_types.USED:
            result.held += 1
            continue
        result.used += 1
        if case.expected in BY_KEY:
            if decision.kind == case.expected:
                result.right += 1
            else:
                result.wrong += 1
                result.confusion[f"{case.expected} -> {decision.kind}"] += 1
        elif decision.stored:  # none / alias, but a tie would be stored
            result.stored_wrongly += 1
            result.confusion[f"{case.expected} -> {decision.kind}"] += 1
    result.chosen = rel_types.choose(scored)
    return result


def report(r: Result) -> str:
    """The numbers as plain lines."""
    judged = r.right + r.wrong
    precision = f"{r.right / judged:.0%}" if judged else "n/a"
    lines = [
        f"cases {r.cases}; threshold {r.threshold:.2f}, margin {r.margin:.2f}",
        f"used {r.used}: right {r.right}, wrong {r.wrong} (precision {precision}); "
        f"held back {r.held}; stored wrongly (none/alias cases) {r.stored_wrongly}",
    ]
    for pair, n in sorted(r.confusion.items()):
        lines.append(f"  {pair}: {n}")
    c = r.chosen
    if c is None:
        lines.append(f"choose: no pair reaches {rel_types.PRECISION_TARGET:.0%} precision")
    else:
        lines.append(
            f"choose: threshold {c.threshold:.3f}, margin {c.margin:.2f} cover {c.used} cases "
            f"at {c.precision:.0%} precision"
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="rel_golden_run", description=__doc__)
    parser.add_argument("--threshold", type=float, default=rel_types.THRESHOLD)
    parser.add_argument("--margin", type=float, default=rel_types.MARGIN)
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    key = os.environ.get("VOYAGE_API_KEY", "")
    if not key:
        print("Set VOYAGE_API_KEY in the environment (it is never printed).", file=sys.stderr)
        return 2
    from dmbot.config import Settings  # the models are the bot's own settings
    from dmbot.memory.voyage import HttpTransport, Voyage

    settings = Settings(discord_token="x", ears_secret="x", voyage_key=key)
    transport = HttpTransport(key)
    voyage = Voyage(
        transport,
        embed_model=settings.voyage_embed_model,
        rerank_model=settings.voyage_rerank_model,
    )

    async def embed(texts: Sequence[str], as_query: bool) -> list[list[float]]:
        return await voyage.embed(texts, query=as_query)

    try:
        result = await evaluate(embed, threshold=args.threshold, margin=args.margin)
    finally:
        await transport.close()
    print(report(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
