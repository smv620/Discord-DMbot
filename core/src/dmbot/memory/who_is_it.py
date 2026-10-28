"""The decision rule for "who is he?" (#2790, slice 1): given the AI's scored candidates for a
pronoun or description ("he", "the king", "his sister"), accept the top one, ask the DM, or keep an
unnamed person. Pure: no AI, no database, no network.

The rule is the owner's (2026-10-28): at 80% or more the answer is accepted; below 40% (or when
"someone not listed" is on top) the relationship is kept with an unnamed person, never dropped;
in between, the top pick must stand clear of the noise among the other candidates, or the DM is
asked. Confidences are percentages (0 to 100). The threshold and the signal-to-noise figure are
constants here because the calibration set (a later slice) is what tunes them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

NOT_LISTED = "someone not listed"  # a real candidate: the person may be none of the names offered
ACCEPT_AT = 80.0
UNNAMED_BELOW = 40.0
MIN_SNR = 1.5  # signal over noise; noise is at least the runner-up, so this caps it at 0.667 x top
MAX_CANDIDATES = 5
SIGMAS = 3.0


class Verdict(Enum):
    ACCEPT = "accept"
    ASK = "ask"  # flagged to the DM as a question
    UNNAMED = "unnamed"  # kept as "Ken's sister (name unknown)"


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    confidence: float  # percent


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    name: str | None  # the accepted name, or the top pick being asked about
    snr: float | None  # only when the rule had to compute it


def noise(others: list[float]) -> float:
    """Mean plus three population standard deviations (divided by n, no zero-fill) of the other
    candidates' confidences."""
    mean = sum(others) / len(others)
    sigma = math.sqrt(sum((x - mean) ** 2 for x in others) / len(others))
    return mean + SIGMAS * sigma


def decide(candidates: list[Candidate]) -> Decision:
    """What to do with the AI's answer. Only the top MAX_CANDIDATES count; a candidate list with
    nothing in it is an unnamed person (the relationship is still a stated fact)."""
    ranked = sorted(candidates, key=lambda c: -c.confidence)[:MAX_CANDIDATES]
    if not ranked:
        return Decision(Verdict.UNNAMED, None, None)
    top = ranked[0]
    if top.name == NOT_LISTED or top.confidence < UNNAMED_BELOW:
        return Decision(Verdict.UNNAMED, None, None)
    if top.confidence >= ACCEPT_AT:
        return Decision(Verdict.ACCEPT, top.name, None)
    others = [c.confidence for c in ranked[1:]]
    if not others:  # nothing to compare with: no signal-to-noise, 40% or more is enough
        return Decision(Verdict.ACCEPT, top.name, None)
    level = noise(others)
    snr = math.inf if level == 0 else top.confidence / level
    return Decision(Verdict.ACCEPT if snr >= MIN_SNR else Verdict.ASK, top.name, snr)
