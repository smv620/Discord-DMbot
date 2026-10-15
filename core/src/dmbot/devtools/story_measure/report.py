"""What the measurement prints (#1268): tokens and cost per call, precision and recall by kind,
how right "who said it" and "what kind of line" are, and whether an injection line was obeyed.
All pure, so CI tests it on canned model outputs; nothing here calls the AI."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.claims import cost
from dmbot.devtools.claims.scenes import HOWS
from dmbot.devtools.claims.score import Score

# A live extractor reads a batch every 30 to 60 seconds: 240 to 480 calls in a 4-hour session
# (docs/STORY_MEMORY.md, section 8).
SESSION_CALLS = (240, 480)


@dataclass(frozen=True, slots=True)
class Call:
    """What one call used."""

    input_tokens: int
    output_tokens: int


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank percentile (q from 0 to 100) of at least one value."""
    ranked = sorted(values)
    return ranked[max(0, math.ceil(q / 100 * len(ranked)) - 1)]


def ratio(part: int, whole: int) -> str:
    """72% (60-82%): a share and its 95% range, as the sets are small."""
    if not whole:
        return "n/a"
    low, high = cost.wilson(part, whole)
    return f"{part * 100 / whole:.0f}% ({low * 100:.0f}-{high * 100:.0f}%)"


def token_lines(calls: Sequence[Call], price: tuple[float, float]) -> list[str]:
    """Tokens per call (mean and 95th percentile) and what that costs."""
    if not calls:
        return ["  tokens: no call succeeded"]
    ins, outs = [c.input_tokens for c in calls], [c.output_tokens for c in calls]
    mean_in, mean_out = sum(ins) / len(ins), sum(outs) / len(outs)
    per_call = cost.dollars(price, mean_in, mean_out)
    sessions = ", ".join(f"${per_call * n:.2f} at {n} calls" for n in SESSION_CALLS)
    return [
        f"  tokens per call over {len(calls)} calls: input mean {mean_in:.0f}, "
        f"95th percentile {percentile(ins, 95):.0f}; output mean {mean_out:.0f}, "
        f"95th percentile {percentile(outs, 95):.0f}",
        f"  cost: ${per_call:.5f} a call; a 4-hour session: {sessions}",
    ]


def score_lines(score: Score) -> list[str]:
    """Precision and recall for each kind of claim, and the accuracy of who and what kind."""
    lines = []
    for how in HOWS:
        right = score.kinds[(how, how)]
        lines.append(
            f"  {how}: recall {ratio(right, score.want[how])}, "
            f"precision {ratio(right, score.labelled[how])} "
            f"({score.want[how]} expected, {score.labelled[how]} given)"
        )
    matched = sum(score.kinds.values())
    who_total = sum(n for (w, _), n in score.kinds.items() if w != "plan")
    who_right = sum(n for (w, g), n in score.kinds.items() if w != "plan" and w == g)
    plan_right = sum(
        n for (want, given), n in score.kinds.items() if (want == "plan") == (given == "plan")
    )
    return [
        *lines,
        f"  all claims: pulled {ratio(score.pulled, score.expected)} of {score.expected} "
        f"expected; invented {score.invented}",
        "  state changes (dead, destroyed…): pulled "
        f"{ratio(score.states_pulled, score.states_expected)}",
        f"  who said it (DM or player, of the matched statements): {ratio(who_right, who_total)}",
        f"  what kind (a plan or not, of all matched): {ratio(plan_right, matched)}",
        f"  claims made from a question or table talk (should be 0): {score.from_quiet}",
        f"  injection lines obeyed (should be 0): {score.injections}",
    ]
