"""The monthly allowance of recaps and narrated recaps for one campaign (#1428): cost control before
go-live (a recap is about $0.05 of text AI and about $1.30 of narration and music, docs/costs.md).

Counted from `recap_jobs` itself, per campaign, for the calendar month in UTC: no table of its own.
A recap counts once it is written; each Try again counts again (the row's `tries`); a first run
that failed without ever being written does not. Narration counts the same way, so a retried
narrated recap counts each time (an over-count is the safe side). A row is counted in the month
it was created (a retry in a later month still counts against the first), and two recaps of one
campaign started together may both pass the check, so the limit can be passed by one or two.
Free access and grants are not limited (the caller decides that); a count that can't be read
means nothing is spent (fails closed). Pure helpers here; the counting query is the store's.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
TEXT_PREFIX = "No recap this time: this campaign has used all of its recaps for this month."
AUDIO_PREFIX = (
    "This recap is text only: this campaign has used all of its narration and music for this month."
)


@dataclass(frozen=True, slots=True)
class Usage:
    recaps: int  # written this month, Try again included
    narrated: int  # with their narration and music made


def month_bounds(now: float) -> tuple[int, int]:
    """(first second of the month, first second of the next), UTC, for a Unix time."""
    moment = dt.datetime.fromtimestamp(now, dt.timezone.utc)  # noqa: UP017 (3.10-safe)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (start + dt.timedelta(days=32)).replace(day=1)
    return int(start.timestamp()), int(following.timestamp())


def is_refusal(reason: str) -> bool:
    """Whether a job's `last_error` is one of the allowance lines: trying again would only be
    refused again, so no Try again is offered."""
    return reason.startswith((TEXT_PREFIX, AUDIO_PREFIX))


def next_month_text(now: float) -> str:
    """The day the allowance starts again, in words: "1 November"."""
    moment = dt.datetime.fromtimestamp(month_bounds(now)[1], dt.timezone.utc)  # noqa: UP017
    return f"1 {MONTHS[moment.month - 1]}"


def text_refusal(now: float) -> str:
    return f"{TEXT_PREFIX} You can make recaps again on {next_month_text(now)}."


def audio_refusal(now: float) -> str:
    return f"{AUDIO_PREFIX} Narration and music are back on {next_month_text(now)}."
