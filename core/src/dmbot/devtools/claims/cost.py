"""What the measurement and a session would cost (#234). Prices are list prices per
million tokens: check the company's current prices before relying on them, and give
others with --price."""

from __future__ import annotations

import math
from dataclasses import dataclass

# US dollars per million tokens: (input, output).
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
SESSION_S = 4 * 3600
CHARS_PER_TOKEN = 3.0  # cautious: real text runs nearer 4, so estimates come out high
AFTER_SESSION_CHUNK_S = 600  # one after-session pass reads ten minutes at a time


def tokens_for(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def dollars(price: tuple[float, float], input_tokens: float, output_tokens: float) -> float:
    return (price[0] * input_tokens + price[1] * output_tokens) / 1_000_000


@dataclass(frozen=True, slots=True)
class Usage:
    """What one model used over the batches."""

    calls: int
    input_tokens: int
    output_tokens: int
    talk_s: float  # how long the batches take to say
    system_share: float  # the instructions' part of the input, by length (both estimated
    # the same way, so the share is fair even where the count isn't)

    def live(self, price: tuple[float, float]) -> float:
        """A 4-hour session read as it's played, a batch at a time like these."""
        per_call = dollars(price, self.input_tokens / self.calls, self.output_tokens / self.calls)
        return per_call * SESSION_S / (self.talk_s / self.calls)

    def after_session(self, price: tuple[float, float]) -> float:
        """One pass over a 4-hour session afterwards, ten minutes per call. Not measured:
        worked out from the batches' rates."""
        system = self.input_tokens * self.system_share / self.calls
        talk = self.input_tokens * (1 - self.system_share) / self.talk_s
        chunks = SESSION_S / AFTER_SESSION_CHUNK_S
        input_tokens = talk * SESSION_S + system * chunks
        return dollars(price, input_tokens, self.output_tokens / self.talk_s * SESSION_S)


def wilson(part: int, whole: int) -> tuple[float, float]:
    """A 95% range for a share measured on a small sample."""
    if whole == 0:
        return 0.0, 1.0
    z, p = 1.96, part / whole
    centre = (p + z * z / (2 * whole)) / (1 + z * z / whole)
    half = z * math.sqrt(p * (1 - p) / whole + z * z / (4 * whole * whole)) / (1 + z * z / whole)
    return max(0.0, centre - half), min(1.0, centre + half)
