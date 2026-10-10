"""The providers' list prices, each with where it was read and when (#945). Never a guess:
a price that was not checked is not here, and `price_of` says so.

Update a price here, with its source and the date checked, and re-run the measuring tool
(`python -m dmbot.devtools.replay --measure`) to refresh docs/costs.md. These are list
prices: any discount, credit or volume plan is not counted.
"""

from __future__ import annotations

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

_ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
# Anthropic, base (uncached) tokens: model id -> (input, output) per million tokens. Core
# uses the first for the off-topic filter, the audio check and the DM sidebar
# (dmbot.ai.DEFAULT_MODELS). A model that is not here has not been checked.
ANTHROPIC_PER_MILLION: dict[str, tuple[Price, Price]] = {
    "claude-haiku-4-5-20251001": (
        Price(1.0, "per million input tokens", _ANTHROPIC, "2026-10-09"),
        Price(5.0, "per million output tokens", _ANTHROPIC, "2026-10-09"),
    ),
}


class UnknownPrice(LookupError):
    """A price nobody has checked: the report says so instead of guessing."""


def price_of(model: str) -> tuple[Price, Price]:
    try:
        return ANTHROPIC_PER_MILLION[model]
    except KeyError:
        raise UnknownPrice(
            f"No price on file for {model!r}: read it from {_ANTHROPIC} and add it to "
            "dmbot.devtools.costs.prices first."
        ) from None
