"""The words of the 🤫 Known or secret? card for the website (#4598). They are the words of
`ui/secret_questions.py`, written without Discord's bold marks; the website may not import the
Discord layer (the import contract), so a test holds them equal (`test_secret_question_words`).
Plain words only; the page escapes whatever a DM typed."""

from __future__ import annotations

from typing import Any

HEADING = "Known or secret?"
WHY = "DMbot never changes what your players know without asking you."
KEEP_KNOWN, MAKE_SECRET = "Keep it known", "Make it secret"
KEEP_BOTH, END_OLD = "Keep both", "End the old one"
KEEP_SECRET, LET_KNOW = "Keep it secret", "Let them know"
UNDO = "Undo"
GONE_LINE = "This one was about something since removed. Either answer just clears it."
NAMES_SHOWN = 5


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def names(items: list[str]) -> str:
    shown = list(items[:NAMES_SHOWN])
    if len(items) > NAMES_SHOWN:
        shown.append(_plural(len(items) - NAMES_SHOWN, "other"))
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def source(label: str) -> str:
    return f'From "{label}".' if label else ""


def make_secret_says(what: str, knowers: list[str], limits: bool) -> str:
    """What Make it secret does, in plain words: DMbot's record, never the game itself."""
    if knowers:
        return f"DMbot stops showing {names(knowers)} as knowing it."
    if limits:
        return "only the players this clue was meant for see it."
    return f"this {what} is hidden from your players in DMbot."


def gone(kind: str) -> dict[str, Any]:
    keep, make = _buttons(kind)
    return {
        "heading": GONE_LINE,
        "keep": {"label": keep, "says": "", "done": ""},
        "make": {"label": make, "says": "", "done": ""},
    }


def _buttons(kind: str) -> tuple[str, str]:
    if kind == "replace":
        return KEEP_BOTH, END_OLD
    if kind == "reveal":
        return KEEP_SECRET, LET_KNOW
    return KEEP_KNOWN, MAKE_SECRET


def card(
    kind: str, item: str, what: str, owner: str, new: str, knowers: list[str], limits: bool
) -> dict[str, Any]:
    """The question's line and what each answer does, as `ui/secret_questions.card_text` words
    them (and `done_text`, for what the page says after each answer)."""
    keep, make = _buttons(kind)
    if kind == "replace":
        heading = f"Did this change? Your players know {item}." + (
            f" Now: {new}." if new else " Something newer says otherwise."
        )
        keeps = f"{KEEP_BOTH}: both stay true, and DMbot won't ask this again."
        makes = f"{END_OLD}: the old one becomes how things were. {UNDO} takes it back."
        kept_done, made_done = (
            "Kept both. DMbot won't ask about this again.",
            (f"Done: {item} is now how things were. {UNDO} takes it back."),
        )
    elif kind == "reveal":
        heading = f"Your players know the name {item}. Only you know it is {owner}."
        keeps = f"{KEEP_SECRET}: they don't learn it is {owner}, and DMbot won't ask this again."
        makes = f"{LET_KNOW}: your players learn that {item} is {owner}. {UNDO} takes it back."
        kept_done, made_done = (
            "Kept secret. DMbot won't ask about this again.",
            (f"Done: your players now know {item} is {owner}. {UNDO} takes it back."),
        )
    else:
        thing = f"{item}, another name of {owner}," if what == "other name" else item
        heading = f"{thing} sounds like a secret, but your players already know it."
        keeps = f"{KEEP_KNOWN}: nothing changes, and DMbot won't ask this again."
        makes = f"{MAKE_SECRET}: {make_secret_says(what, knowers, limits)} {UNDO} takes it back."
        kept_done, made_done = (
            "Left as known. DMbot won't ask about this again.",
            (f"Done: {item} is a secret now. {UNDO} takes it back."),
        )
    return {
        "heading": heading,
        "keep": {"label": keep, "says": keeps, "done": kept_done},
        "make": {"label": make, "says": makes, "done": made_done},
    }
