"""The fixed opening and closing lines of every recap DMbot writes (#2253).

A recap opens with "Previously, on <campaign>…" and closes with "And so the party continues the
adventure of <campaign>." so that next session, when the DM or a player reads it aloud, DMbot can
find it in the transcript and not retell it as news. The lines are added by code after the writer
and every check are done, so they are never a beat, never a claim, and never counted in the length
target or the word count; the narration says them because they are part of the text. Only the
campaign's display name is used, and never a secret one: a name that is (or contains) a secret name
is left out, and the lines go without it. A recap already written is not changed (nothing here runs
on one), and wrapping twice changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from dmbot.recaps.text import names_a_secret

OPENING = "Previously, on {name}…"
CLOSING = "And so the party continues the adventure of {name}."
OPENING_BARE = "Previously…"
CLOSING_BARE = "And so the party continues the adventure."
ROOM = 400  # characters the web editor allows beyond the longest draft: both lines and their gap

_CUE_BRACKETS = re.compile(r"[《》()]")
_OPENS = re.compile(r"^\s*Previously,?\s+on\b.{0,200}?…|^\s*Previously…", re.IGNORECASE | re.DOTALL)
_CLOSES = re.compile(
    r"And so the party continues the adventure(?: of [^\n]{1,200}?)?\.\s*$", re.IGNORECASE
)


def usable_name(name: str, secret_names: Collection[str]) -> str:
    """The campaign's name as it may be spoken: one line, no cue brackets, nothing secret. An
    empty result means "go without the name"."""
    clean = " ".join(_CUE_BRACKETS.sub(" ", name).split())[:100]
    if not clean or names_a_secret(clean, secret_names, []):
        return ""
    return clean


def lines(name: str, secret_names: Collection[str] = ()) -> tuple[str, str]:
    """(opening, closing) for this campaign."""
    use = usable_name(name, secret_names)
    if not use:
        return OPENING_BARE, CLOSING_BARE
    return OPENING.format(name=use), CLOSING.format(name=use)


def wrap(summary: str, name: str, secret_names: Collection[str] = ()) -> str:
    """The recap's text with its two fixed lines, each its own paragraph. Idempotent: a text that
    already has a line keeps it and gets only the one it lacks."""
    opening, closing = lines(name, secret_names)
    body = summary.strip()
    parts = [] if _OPENS.search(body) else [opening]
    parts.append(body)
    if not _CLOSES.search(body):
        parts.append(closing)
    return "\n\n".join(parts)


def without(summary: str) -> str:
    """The recap's text without its fixed lines: what the length target and word counts see, and
    what the next recap is given as "last time" (so the writer is not shown lines to copy)."""
    body = summary.strip()
    body = _OPENS.sub("", body, count=1).strip()
    return _CLOSES.sub("", body).strip()
