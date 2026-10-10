"""The arithmetic of cost per table-hour (#945). Pure: no files, no network.

A session is measured by the twin (`measure.py`): how long the table was, how much speech
went to speech-to-text, and how many AI calls each feature made with how many tokens. This
turns those into dollars per table-hour for three cases of how much a real table talks,
because the twin's scripts are read densely, nearly without pauses, and real play is not.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.ai import DEFAULT_MODELS
from dmbot.devtools.costs import prices

# Speech-minutes sent to speech-to-text per table-hour at a real table. PLAN.md's earlier
# guess was "roughly 36-60"; these three cases use that range until run 8 measures one
# (docs/costs.md says so). The twin's own density is shown beside them.
SPEECH_MINUTES_PER_HOUR = {"low": 36.0, "typical": 48.0, "high": 60.0}
# The DM sidebar (#935): questions asked in a table-hour. An assumption, not a measure.
SIDEBAR_QUESTIONS_PER_HOUR = 6.0
FILTER = "off-topic filter"  # scales with how much is said
SIDEBAR = "DM sidebar"  # scales with the questions asked


@dataclass(frozen=True, slots=True)
class Usage:
    """AI use of one feature: calls, and the tokens they sent and got back."""

    calls: float = 0.0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    model: str = DEFAULT_MODELS.fast
    estimated: bool = True  # tokens counted by size, not by the AI service

    def __add__(self, other: Usage) -> Usage:
        if other.model != self.model:
            raise ValueError("can't add the use of two different models")
        return Usage(
            self.calls + other.calls,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.model,
            self.estimated or other.estimated,
        )

    def scaled(self, factor: float) -> Usage:
        return Usage(
            self.calls * factor,
            self.input_tokens * factor,
            self.output_tokens * factor,
            self.model,
            self.estimated,
        )


@dataclass(frozen=True, slots=True)
class Session:
    """What the twin measured for one recording or script."""

    name: str
    table_s: float  # how long the table talked, start to end
    speech_s: float  # speech sent to speech-to-text (pieces under the minimum left out)
    lines: int  # pieces of speech
    ai: Mapping[str, Usage] = field(default_factory=dict)  # feature -> use
    measured_audio: bool = True  # False: timing estimated from the script's words


def estimate_tokens(text: str) -> int:
    """Tokens in `text`, estimated by size (about 3.5 characters each for English), rounded
    up. The AI service counts the real number; this is for the plan, flagged as an estimate."""
    return math.ceil(len(text) / 3.5)


def stt_dollars(speech_minutes: float) -> float:
    return speech_minutes * prices.DEEPGRAM_PRERECORDED.dollars


def ai_dollars(usage: Usage) -> float:
    price_in, price_out = prices.price_of(usage.model)
    return (
        usage.input_tokens * price_in.dollars + usage.output_tokens * price_out.dollars
    ) / 1_000_000


@dataclass(frozen=True, slots=True)
class PerHour:
    """One case: dollars per table-hour, by part."""

    case: str
    speech_minutes: float
    stt: float
    ai: Mapping[str, float]
    hosting: float | None  # None: the monthly cost was not given

    @property
    def total(self) -> float:
        return self.stt + sum(self.ai.values()) + (self.hosting or 0.0)

    @property
    def total_without_hosting(self) -> float:
        return self.stt + sum(self.ai.values())


def per_feature(sessions: Sequence[Session]) -> tuple[dict[str, Usage], float]:
    """AI use per feature across the sessions, and the total speech-minutes they hold."""
    out: dict[str, Usage] = {}
    for session in sessions:
        for feature, usage in session.ai.items():
            out[feature] = out.get(feature, Usage()) + usage
    return out, sum(s.speech_s for s in sessions) / 60


def table_hour(
    sessions: Sequence[Session],
    *,
    sidebar: Usage,
    speech_minutes: Mapping[str, float] = SPEECH_MINUTES_PER_HOUR,
    questions_per_hour: float = SIDEBAR_QUESTIONS_PER_HOUR,
    hosting_monthly: float | None = None,
    table_hours_per_month: float | None = None,
) -> list[PerHour]:
    """Dollars per table-hour for each case. The filter's calls per speech-minute come from
    the sessions; the sidebar is `questions_per_hour` calls of the `sidebar` per-call use.
    Hosting is the monthly cost over the table-hours a month, only if both are given."""
    measured, total_minutes = per_feature(sessions)
    if total_minutes <= 0:
        raise ValueError("the sessions hold no speech")
    hosting = (
        hosting_monthly / table_hours_per_month
        if hosting_monthly is not None and table_hours_per_month
        else None
    )
    cases: list[PerHour] = []
    for case, minutes in speech_minutes.items():
        ai: dict[str, float] = {}
        for feature, usage in measured.items():
            ai[feature] = ai_dollars(usage.scaled(minutes / total_minutes))
        ai[SIDEBAR] = ai_dollars(sidebar.scaled(questions_per_hour))
        cases.append(PerHour(case, minutes, stt_dollars(minutes), ai, hosting))
    return cases
