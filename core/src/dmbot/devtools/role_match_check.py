"""Scores matching proposed story roles to the seeded ones on the golden set, counts only (#3172).

    VOYAGE_API_KEY=... python -m dmbot.devtools.role_match_check

For each golden case (`memory.role_golden`, invented roles) it finds the closest seeded role by
meaning (`memory.role_match`: a role's text is its description and examples, never `not_this`),
then prints, with no role's words, only numbers for every threshold from 0.70 to 0.90 and a few
margins:

- `used`: matches the pair lets through; `right` / `wrong` among the cases that have a seeded role;
- `precision`: right of used (cases with a seeded role); `coverage`: right of all such cases;
- `none wrong`: cases with no seeded role (`NONE`) that a used match would wrongly swallow;
- the margins (best minus second) of right and wrong best matches, as quartiles;
- `choose`: among the pairs with no `none wrong` and precision at least 90% (the rule proposed on
  #3172), the one with the most right matches, then the more precise, then the stricter; with its
  coverage. Supervisor rules the pair and the switch from these numbers.

The model is `VOYAGE_EMBED_MODEL` as the bot reads it (default `voyage-3.5`), printed first, so the
numbers are for the model the bot uses.

The key is read from the environment and never printed. Every text is embedded as a document (a
role is compared with roles). Nothing is written to a database. The pure part (`evaluate`) takes the
embedder as an argument, so it is tested without the network.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from dmbot.memory import role_golden, role_match, roles

Embed = Callable[[Sequence[str]], Awaitable[list[list[float]]]]

THRESHOLDS = tuple(round(0.70 + 0.02 * i, 2) for i in range(11))  # 0.70 to 0.90
MARGINS = (0.0, 0.02, 0.05, 0.08)
PRECISION_TARGET = 0.90  # proposed on #3172; Supervisor rules it


@dataclass(frozen=True, slots=True)
class Row:
    threshold: float
    margin: float
    used: int
    right: int
    wrong: int
    none_wrong: int
    typed: int  # cases that have a seeded role

    @property
    def precision(self) -> float:
        judged = self.right + self.wrong
        return self.right / judged if judged else 1.0

    @property
    def coverage(self) -> float:
        return self.right / self.typed if self.typed else 0.0


@dataclass(slots=True)
class Result:
    cases: int = 0
    typed: int = 0
    nones: int = 0
    unmatched: int = 0  # no best match at all (a bad vector)
    rows: list[Row] = field(default_factory=list)
    right_margins: list[float] = field(default_factory=list)
    wrong_margins: list[float] = field(default_factory=list)
    chosen: Row | None = None


async def evaluate(embed: Embed, cases: Sequence[role_golden.Case] = role_golden.CASES) -> Result:
    """Match every case against the seeded roles and count, for every threshold and margin."""
    seeded = [r for r in roles.SEEDED_ROLES if r.code != roles.COMMONER]
    pool = dict(
        zip((r.code for r in seeded), await embed([r.embed_text for r in seeded]), strict=True)
    )
    vectors = await embed([c.text for c in cases])
    best: list[tuple[role_golden.Case, role_match.Best]] = []
    result = Result(cases=len(cases))
    for case, vector in zip(cases, vectors, strict=True):
        result.typed += case.expected != role_golden.NONE
        result.nones += case.expected == role_golden.NONE
        b = role_match.nearest(vector, pool)
        if b is None:
            result.unmatched += 1
            continue
        best.append((case, b))
        if case.expected != role_golden.NONE:
            margin = b.score - b.second_score
            (result.right_margins if b.code == case.expected else result.wrong_margins).append(
                margin
            )
    for threshold in THRESHOLDS:
        for margin in MARGINS:
            used = right = wrong = none_wrong = 0
            for case, b in best:
                code, _why = role_match.decide(b, threshold=threshold, margin=margin)
                if code is None:
                    continue
                used += 1
                if case.expected == role_golden.NONE:
                    none_wrong += 1
                elif code == case.expected:
                    right += 1
                else:
                    wrong += 1
            result.rows.append(Row(threshold, margin, used, right, wrong, none_wrong, result.typed))
    fits = [
        r for r in result.rows if r.none_wrong == 0 and r.right and r.precision >= PRECISION_TARGET
    ]
    if fits:  # (the most covered, then the stricter)
        result.chosen = max(fits, key=lambda r: (r.right, r.precision, r.threshold, r.margin))
    return result


def _quartiles(values: Sequence[float]) -> str:
    if len(values) < 2:
        return "n/a" if not values else f"{values[0]:.3f}"
    q = statistics.quantiles(values, n=4)
    return f"{q[0]:.3f} / {q[1]:.3f} / {q[2]:.3f}"


def report(r: Result) -> str:
    """The numbers as plain lines."""
    lines = [
        f"cases {r.cases}: {r.typed} with a seeded role, {r.nones} with none; "
        f"{r.unmatched} unmatched",
        f"margins of right best matches (quartiles): {_quartiles(r.right_margins)}",
        f"margins of wrong best matches (quartiles): {_quartiles(r.wrong_margins)} "
        f"({len(r.wrong_margins)} wrong at any threshold)",
        "threshold margin  used right wrong precision coverage none_wrong",
    ]
    for row in r.rows:
        lines.append(
            f"{row.threshold:9.2f} {row.margin:6.2f} {row.used:5d} {row.right:5d} {row.wrong:5d}"
            f" {row.precision:9.0%} {row.coverage:8.0%} {row.none_wrong:10d}"
        )
    c = r.chosen
    if c is None:
        lines.append(f"choose: no pair has no none_wrong and {PRECISION_TARGET:.0%} precision")
    else:
        lines.append(
            f"choose: threshold {c.threshold:.2f}, margin {c.margin:.2f}: {c.right} right of "
            f"{r.typed} ({c.coverage:.0%} coverage) at {c.precision:.0%} precision, none_wrong 0"
        )
    return "\n".join(lines)


async def main_async(_args: argparse.Namespace) -> int:
    key = os.environ.get("VOYAGE_API_KEY", "")
    if not key:
        print("Set VOYAGE_API_KEY in the environment (it is never printed).", file=sys.stderr)
        return 2
    from dmbot.config import Settings  # the model is the bot's own setting
    from dmbot.memory.voyage import HttpTransport, Voyage

    model = os.environ.get("VOYAGE_EMBED_MODEL", "") or "voyage-3.5"  # (the bot's default)
    settings = Settings(
        discord_token="x", ears_secret="x", voyage_key=key, voyage_embed_model=model
    )
    print(f"model {settings.voyage_embed_model}")
    transport = HttpTransport(key)
    voyage = Voyage(
        transport,
        embed_model=settings.voyage_embed_model,
        rerank_model=settings.voyage_rerank_model,
    )

    async def embed(texts: Sequence[str]) -> list[list[float]]:
        return await voyage.embed(texts)

    try:
        result = await evaluate(embed)
    finally:
        await transport.close()
    print(report(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="role_match_check", description=__doc__)
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
