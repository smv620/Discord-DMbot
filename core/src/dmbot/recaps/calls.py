"""What a recap test run reports about each AI call (#2053): the stage and try, the tier, the
tokens and the room they had, why the answer stopped, the word count, the kinds of check that
failed and the grader's numbers. Counts and fixed words only: never a line of the transcript, a
name, a quote or any of the recap's own text, because a test report is posted to a public issue.
Pure: the worker fills these in and a test tool prints them."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# The service's own words for why an answer stopped; anything else is reported as "other".
STOPS = frozenset({"end_turn", "max_tokens", "refusal", "stop_sequence", "tool_use", "pause_turn"})
FINAL = "final"  # the outcome of a draft that stood the ordinary way
# A job's state when a call is made -> the step that call belongs to.
STAGE_OF_STATE = {"queued": "beats", "beats": "beats", "checked": "prose", "prose": "title"}

# (kind, words of a problem that put it there), tried in order. A problem is a sentence the
# checks wrote; the kind is the fixed word the report shows instead of it.
# (the order matters: the first that fits wins, so a sentence about the word count is "length")
_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("facts", ("the beats do not say", "leaves out beat")),  # (first: it holds the draft's words)
    ("declined", ("was declined",)),
    ("cut off", ("was cut off",)),
    ("shape", ("answer with", "answer in the ")),
    ("secret", ("keeps secret",)),
    ("ending", ("end exactly",)),
    ("quotes", ("quotation marks",)),
    ("copying", ("word for word",)),
    ("length", (" words", "characters")),
    ("layout", ("emotion cue", "emotion sections", "exactly one music prompt")),
    ("music", ("music prompt",)),
    ("names", ("names that are not", "not in the beat sheet")),
    ("title", ("the title is over",)),
    ("housekeeping", ("housekeeping",)),
    ("beats", ("beat(s) cut", "usable beat", "in no beat")),
)


def problem_kind(problem: str) -> str:
    """The fixed word for one problem; "other" for one that matches none."""
    low = problem.casefold()
    for kind, needles in _KINDS:
        if any(n in low for n in needles):
            return kind
    return "other"


def kinds(problems: Iterable[str]) -> list[str]:
    """The kinds of these problems, each once, in the order they first came."""
    out: list[str] = []
    for problem in problems:
        kind = problem_kind(problem)
        if kind not in out:
            out.append(kind)
    return out


@dataclass(slots=True)
class CallEvent:
    """One AI call. The worker makes it when the call returns and fills in the rest as the
    answer is checked, so an observer holding it sees the whole story."""

    stage: str  # beats, prose (or write and format), facts, grade or title
    attempt: int  # the try, from 1
    tier: str  # fast or careful
    tokens_in: int
    tokens_out: int
    limit: int  # the room the answer was given (max tokens)
    stop: str  # end_turn, max_tokens, refusal ...: the service's own fixed word
    words: int | None = None  # the narration's words, for a prose answer that could be read
    checks: list[str] | None = None  # None: not checked; []: passed; else the kinds that failed
    grade: dict[str, int] = field(default_factory=dict)
    facts: dict[str, int] = field(default_factory=dict)  # the fact check: kind -> how many (#2064)
    outcome: str = ""  # on the prose call whose draft became the recap


def line(event: CallEvent) -> str:
    """One line, like `prose try 2: careful, 1204/2310 (limit 2559), stop: max_tokens, cut off at
    2559, 173 words, checks: length`."""
    parts = [
        f"{event.stage} try {event.attempt}: {event.tier}",
        f"{event.tokens_in}/{event.tokens_out} (limit {event.limit})",
        f"stop: {event.stop or 'unknown'}",
    ]
    if event.stop == "max_tokens":  # the next run shows whether the room is now enough (#2086)
        parts.append(f"cut off at {event.limit}")
    if event.words is not None:
        parts.append(f"{event.words} words")
    if event.checks is not None:
        parts.append("checks: " + (", ".join(event.checks) if event.checks else "passed"))
    if event.stage == "facts":
        found = ", ".join(f"{k} {n}" for k, n in event.facts.items())
        parts.append("flagged: " + (found or "nothing"))
    if event.grade:
        parts.append("grade " + " ".join(f"{k} {v}" for k, v in event.grade.items()))
    return ", ".join(parts)


def which_draft(events: Sequence[CallEvent]) -> str:
    """Which prose draft became the recap, in fixed words; "" when none did."""
    for event in events:
        if event.stage in ("prose", "write") and event.outcome:
            how = "" if event.outcome == FINAL else f" ({event.outcome})"
            return f"Draft that became the recap: try {event.attempt}{how}"
    return ""


def section(events: Sequence[CallEvent]) -> list[str]:
    """The report's "Calls" section: one line per call, then which draft stood."""
    if not events:
        return []
    rows = ["", f"Calls ({len(events)}):", *(f"  {line(e)}" for e in events)]
    if chosen := which_draft(events):
        rows.append(f"  {chosen}")
    return rows
