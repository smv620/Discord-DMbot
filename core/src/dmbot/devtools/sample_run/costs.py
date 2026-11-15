"""What a sample run cost, step by step, in one block (#4087).

Three places know the money, and none of them knew all of it: the careful-model decisions count
their own tokens and dollars (`decide.Run`); the bot's own AI work (the lore read, the session
import) is on the credits ledger, which holds credits and never tokens (`ai_credit_spend`, 0418:
1 credit = $0.001); and the recap tool prints its tokens and spend in its report. This module only
turns those three into lines and a total. Counts and dollars only: nothing here ever holds a word
of a lore document, a transcript or a recap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

USD_PER_CREDIT = 0.001  # `credits.MICRO_PER_CREDIT`: 1 credit = 1,000 micro-dollars

# The ledger's job kinds, in the words of the sample run's steps.
JOB_NAMES = {
    "lore": "lore read",
    "import": "session import (cleaning and story read)",
}


@dataclass(frozen=True, slots=True)
class CostLine:
    name: str
    dollars: float
    tokens_in: int | None = None  # None: the source holds no token counts (the credits ledger)
    tokens_out: int | None = None

    def text(self) -> str:
        if self.tokens_in is None or self.tokens_out is None:
            tokens = "tokens not recorded"
        else:
            tokens = f"tokens in {self.tokens_in}, out {self.tokens_out}"
        return f"- {self.name}: {tokens}, cost ${self.dollars:.2f}"


def ledger_lines(credits_by_job: dict[str, int]) -> list[CostLine]:
    """One line for each kind of job the bot's AI did for the run, from the credits ledger. A kind
    with no price on a line of its own is still listed under its own name, never dropped."""
    return [
        CostLine(JOB_NAMES.get(kind, kind), credits * USD_PER_CREDIT)
        for kind, credits in sorted(credits_by_job.items())
    ]


def decision_line(tokens_in: int, tokens_out: int, dollars: float) -> CostLine:
    """The careful model's decisions, every stage together."""
    return CostLine("AI decides (lore and session)", dollars, tokens_in, tokens_out)


_SPEND = re.compile(r"tokens in (\d+), out (\d+); spend ([^\n]*)")
_MONEY = re.compile(r"\$(\d+(?:\.\d+)?)")


def recap_line(report: str) -> CostLine | None:
    """The recap's tokens and spend, read from the one "Calls: …; tokens in N, out N; spend $X"
    line of the recap tool's report. None when the line is not there (the recap was not made, or
    the model had no price on file). Only the numbers are taken; the rest of the report, which
    holds the recap's words, is never kept."""
    found = _SPEND.search(report)
    if found is None:
        return None
    prices = [float(m) for m in _MONEY.findall(found.group(3))]
    if not prices:
        return None
    return CostLine("recap (no audio)", sum(prices), int(found.group(1)), int(found.group(2)))


def block(lines: list[CostLine]) -> list[str]:
    """The lines, then the total (the sum of the lines; 0 for a run with no paid step)."""
    return [
        "Cost of this run:",
        *[line.text() for line in lines],
        f"total cost ${sum(line.dollars for line in lines):.2f}",
    ]
