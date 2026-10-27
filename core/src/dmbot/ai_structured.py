"""Three passes for an AI step that must produce JSON (#2714): a strong model drafts the answer in
free text, a fast model puts the draft into the step's JSON schema, and the caller's own check
decides whether the result is good. A failed check is tried once more on a stronger tier; if that
fails too the caller gets "not structured" and keeps using today's way of asking.

Why: reasoning and formatting in one call is where truncation and malformed keys come from. The
schema pass uses the same JSON-schema mechanism as every other structured step (`schema=` on the
tier client, #2039), so no provider differs here. The adapter only sees what its caller sends, so
consent and campaign isolation are the caller's, unchanged; every call goes through the tier
clients, so it is counted by the same usage limits as any other. Telemetry is counts, never words.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dmbot.ai import OUTPUT_FLOOR, AIError, Reply

log = logging.getLogger(__name__)

OK = "ok"  # the first format pass was accepted
ESCALATED = "escalated"  # accepted after the stronger tier redid the format pass
NOT_STRUCTURED = "not_structured"  # both format passes failed the caller's check
CUT = "cut"  # the draft stopped at the length limit: it is not formatted, it is reported
REFUSED = "refused"  # the service declined: nothing was formatted
EMPTY = "empty"  # the draft came back with no words: it is reported, never formatted

# (The draft is the only input of the format pass: the transcript never reaches it.)
FORMAT_SYSTEM = (
    "You are a formatter. Put the DRAFT you are given into the required JSON shape. Use only what "
    "the draft says: do not add, change, infer or resolve anything. If the draft does not say "
    "something the shape asks for, leave it empty."
)


class Tier(Protocol):
    """What the adapter needs from a tier client (`dmbot.ai.TierClient` has it)."""

    async def complete(
        self, system: str, text: str, *, max_tokens: int = ..., schema: dict[str, Any] | None = ...
    ) -> Reply: ...


@dataclass(frozen=True, slots=True)
class Result[T]:
    """`value` is the caller's checked result, or None when it is "not structured" and the
    caller falls back. `status` says why, in one word of the constants above."""

    value: T | None
    status: str
    passes: int  # model calls made (a draft and one or two format passes)

    @property
    def structured(self) -> bool:
        return self.value is not None


@dataclass(slots=True)
class Counts:
    """Counts only (never a word of a transcript, a draft or an answer)."""

    runs: int = 0
    passes: int = 0
    escalations: int = 0
    fallbacks: int = 0  # runs that ended "not structured", cut or refused
    schema_failures: int = 0  # format passes the caller's check turned down
    cuts: int = 0  # runs that ended cut (a draft, or the last format pass, hit the length limit)
    refusals: int = 0
    empties: int = 0
    errors: int = 0  # runs a tier call raised AIError in (out of funds, service down)

    def as_dict(self) -> dict[str, int]:
        return {
            "runs": self.runs,
            "passes": self.passes,
            "escalations": self.escalations,
            "fallbacks": self.fallbacks,
            "schema_failures": self.schema_failures,
            "cuts": self.cuts,
            "refusals": self.refusals,
            "empties": self.empties,
            "errors": self.errors,
        }


STATS = Counts()  # for the whole bot's run, read by `recap_run --structured` and the logs


async def structured[T](
    system: str,
    text: str,
    schema: dict[str, Any],
    validate: Callable[[str], T | None],
    *,
    draft: Tier,
    fmt: Tier,
    escalate: Tier | None = None,
    draft_tokens: int = OUTPUT_FLOOR,
    format_tokens: int = OUTPUT_FLOOR,
    stage: str = "-",
    counts: Counts | None = None,
) -> Result[T]:
    """Run the three passes. `system`/`text` are the step's own prompt and input, asked of the
    `draft` tier in free text (its prompt should say what shape the draft should take, to make the
    format pass easy). `validate` gets the format pass's JSON text and returns the step's checked
    value or None: it is the check the step already has. `escalate` is the stronger tier for the
    one retry (None: no retry). Raises AIError as any tier call does (out of funds included).
    `stage` names the step in the log (counts only)."""
    c = counts if counts is not None else STATS
    c.runs += 1
    try:
        return await _run(system, text, schema, validate, draft, fmt, escalate, draft_tokens,
                          format_tokens, stage, c)  # fmt: skip
    except AIError:
        c.errors += 1
        raise


async def _run[T](
    system: str,
    text: str,
    schema: dict[str, Any],
    validate: Callable[[str], T | None],
    draft: Tier,
    fmt: Tier,
    escalate: Tier | None,
    draft_tokens: int,
    format_tokens: int,
    stage: str,
    c: Counts,
) -> Result[T]:
    passes = 0
    first = await draft.complete(system, text, max_tokens=draft_tokens)
    passes += 1
    c.passes += 1
    if first.refused:
        return _end(c, stage, REFUSED, passes)
    if first.cut:  # a cut draft is not formatted: its end is missing, and it is said so
        return _end(c, stage, CUT, passes)
    if not first.text.strip():  # nothing to format: a formatter told to leave gaps empty would
        return _end(c, stage, EMPTY, passes)  # "succeed" on nothing
    last = NOT_STRUCTURED
    for retry, tier in enumerate((fmt, escalate)):
        if tier is None:
            break
        if retry:
            c.escalations += 1
        reply = await tier.complete(
            FORMAT_SYSTEM, first.text, max_tokens=format_tokens, schema=schema
        )
        passes += 1
        c.passes += 1
        if reply.refused:  # (the stronger tier may still take what the fast one declined)
            last = REFUSED
            continue
        value = None if reply.cut else validate(reply.text)  # (a cut answer is never valid JSON)
        if value is not None:
            return _end(c, stage, ESCALATED if retry else OK, passes, value)
        c.schema_failures += 1
        last = CUT if reply.cut else NOT_STRUCTURED
    return _end(c, stage, last, passes)


def _end[T](c: Counts, stage: str, status: str, passes: int, value: T | None = None) -> Result[T]:
    if value is None:
        c.fallbacks += 1
        if status == CUT:
            c.cuts += 1
        elif status == REFUSED:
            c.refusals += 1
        elif status == EMPTY:
            c.empties += 1
    log.info("Structured output: stage=%s result=%s passes=%d", stage, status, passes)
    return Result(value, status, passes)
