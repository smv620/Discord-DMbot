"""The fixed opening and closing lines of every recap DMbot writes (#2253).

A recap opens with "Previously, on <campaign>…" and closes with "And so we continue, the <epic
adventure> of <campaign>." (one of ten endings, rotating per campaign: each recap takes the next
after the previous recap's, so two in a row end differently) so that next session, when the DM or
a player reads it aloud, DMbot can find it in the transcript and not retell it as news. The lines
are added by code after the writer and every check are done, so they are never a beat, never a
claim, and never counted in the length target or the word count; the narration says them because
they are part of the text. Only the
campaign's display name is used, and never a secret one: a name that is (or contains) a secret name
is left out, and the lines go without it. A recap already written is not changed (nothing here runs
on one), and wrapping twice changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from dmbot.recaps.text import names_a_secret

OPENING = "Previously, on {name}…"
# The ten endings, in the owner's order (2026-10-21); the order is the rotation, so it is fixed.
ENDINGS = (
    "epic adventure",
    "great adventure",
    "legendary adventure",
    "grand adventure",
    "heroic adventure",
    "epic tale",
    "epic saga",
    "epic journey",
    "perilous adventure",
    "epic quest",
)
CLOSING = "And so we continue, the {ending} of {name}."
OPENING_BARE = "Previously…"
CLOSING_BARE = "And so we continue, the {ending}."
ROOM = 400  # characters the web editor allows beyond the longest draft: both lines and their gap

_CUE_BRACKETS = re.compile(r"[《》()]")
_DOTS = re.compile(r"…|\.{2,}")
# The fixed lines are paragraphs of their own (that is how `wrap` writes them), so a story that
# merely starts "Previously on the road they walked" or ends a sentence the same way as the closing
# line is never taken for them: the opening must be the text's first paragraph and end in an
# ellipsis (three dots too, since a DM may retype it), and the closing the last paragraph.
_OPENS = re.compile(r"\A\s*Previously(?:,\s+on [^\n]{1,200}?)?(?:…|\.{3})[ \t]*(?:\n\s*\n|\Z)")
# The closing is found by its shape, loosely ("and so we continue … of <name>."), so any of the ten
# endings, a garbled one and the first draft's wording are all taken for it. It must be the last
# paragraph and stay on one line.
_CLOSES = re.compile(
    r"(?:\A|\n\s*\n)[ \t]*And so (?:we|the party) continues?\b[^\n]{0,300}?[.!…][ \t]*\Z",
    re.IGNORECASE,
)


def usable_name(name: str, secret_names: Collection[str]) -> str:
    """The campaign's name as it may be spoken: one line, no cue brackets, nothing secret. An
    empty result means "go without the name"."""
    # (A cue bracket or an ellipsis in a name would break the text or the lines' own shape, so they
    # become spaces: "Dragon (Heist)" is spoken "Dragon Heist".)
    clean = " ".join(_DOTS.sub(" ", _CUE_BRACKETS.sub(" ", name)).split())[:100]
    if not clean or names_a_secret(clean, secret_names, []):
        return ""
    return clean


def ending_of(summary: str) -> int | None:
    """Which of the ten endings a recap closes with (its place in ENDINGS), or None when it has no
    closing line or a garbled one: the next recap then starts the rotation again."""
    found = _CLOSES.search(summary.strip())
    if found is None:
        return None
    text = found.group().lower()
    # (Longest first so "epic adventure" is not read as a shorter variant's tail.)
    for index in sorted(range(len(ENDINGS)), key=lambda i: -len(ENDINGS[i])):
        if f"the {ENDINGS[index]}" in text:
            return index
    return None


def next_ending(last: int | None) -> int:
    """The ending after the previous recap's; the first for a campaign's first recap."""
    return 0 if last is None else (last + 1) % len(ENDINGS)


def lines(name: str, secret_names: Collection[str] = (), ending: int = 0) -> tuple[str, str]:
    """(opening, closing) for this campaign, the closing using ENDINGS[ending]."""
    use = usable_name(name, secret_names)
    word = ENDINGS[ending % len(ENDINGS)]
    if not use:
        return OPENING_BARE, CLOSING_BARE.format(ending=word)
    return OPENING.format(name=use), CLOSING.format(ending=word, name=use)


def wrap(summary: str, name: str, secret_names: Collection[str] = (), ending: int = 0) -> str:
    """The recap's text with its two fixed lines, each its own paragraph. Idempotent: a text that
    already has a line keeps it and gets only the one it lacks."""
    opening, closing = lines(name, secret_names, ending)
    body = summary.strip()
    parts = [] if _OPENS.search(body) else [opening]
    parts.append(body)
    if not _CLOSES.search(body):
        parts.append(closing)
    return "\n\n".join(parts)


def without(summary: str) -> str:
    """The recap's text without its fixed lines: what the length target and word counts see, and
    what the next recap is given as "last time" (so the writer is not shown lines to copy)."""
    body = _OPENS.sub("", summary.strip(), count=1).strip()
    return _CLOSES.sub("", body).strip()
