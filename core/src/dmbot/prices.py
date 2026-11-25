"""The prices of the paid services DMbot records the cost of (#3672), each with where it was read
and when. One place: `devtools/costs/prices.py` (the measuring tool's) imports from here. Never a
guess: a service with no checked price is recorded in units only, as unpriced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Price:
    dollars: float
    unit: str
    source: str  # the page it was read from
    checked: str  # the date it was read, YYYY-MM-DD


# Deepgram Nova-3, English, pay as you go. Core sends each piece of speech as a short file
# (the pre-recorded API, dmbot.transcription.deepgram), billed per second.
DEEPGRAM_PRERECORDED = Price(
    0.0043, "per audio minute (billed per second)", "https://deepgram.com/pricing", "2026-10-09"
)
# For reference only: core does not use the streaming API.
DEEPGRAM_STREAMING = Price(
    0.0048, "per audio minute (billed per second)", "https://deepgram.com/pricing", "2026-10-09"
)


def deepgram_micro_usd(seconds: float) -> int:
    """What Deepgram charges for this much audio, in millionths of a dollar, rounded up
    (billed per second at the pre-recorded list price)."""
    return math.ceil(max(0.0, seconds) * DEEPGRAM_PRERECORDED.dollars / 60 * 1_000_000)
