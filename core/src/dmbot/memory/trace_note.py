"""Where each name of a lore read ends, for the per-name trace (#5106; docs/design/session_names.md,
rule 6: every candidate ends with a fixed reason). Dev tools only.

The lore read's steps (`lore_read`, `background`, `ui/name_background`) call `note` where a name is
listed, dropped, folded or stored. It does nothing unless a caller is `collecting()`: the bot does
that only when the name trace is on for this server and the campaign is a sample one
(`name_trace.write` has the same gates). A note is one context lookup, so the read costs the same
with the trace off. The collector is a context variable, so the read's own tasks (pass 2's
slices) write to the same list without the steps passing it along.

No dmbot imports: `background` is imported by `name_reader`, which `name_trace` imports, so the
steps cannot import `name_trace` itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# (name, reason, why): `why` is the name it folded into, when it folded
Note = tuple[str, str, str]

_ROWS: ContextVar[list[Note] | None] = ContextVar("dmbot_lore_trace", default=None)

# How a lore name ended: fixed words (the session reasons with the same meaning share their word)
LISTED = "listed"  # pass 1 listed it (a later row says what happened next, if anything did)
KNOWN = "known_name"  # the campaign has it (the bot tells "the DM said it isn't a name" apart)
COMMON = "common_word"  # an ordinary word or a game term DMbot never offers
NOT_IN_TEXT = "not_in_text"  # pass 2 gave it, but the text doesn't hold it as whole words
ORDINARY = "ordinary_word"  # a pronoun or plain word the text doesn't call a name
SHAPE = "number_time_or_address"
TOO_LONG = "too_long"  # over the name's length: a sentence, not a name
SLICE_UNREAD = "slice_unread"  # its pass-2 slice failed or was still cut when halved
TIME_LIMIT = "time_limit"  # its pass-2 slice was still running when the read's time was up
SPLIT = "split_into_parts"  # a list of names ("A, B and C"), read as each of them
RULES = "rules_word"  # a monster or spell the campaign's rules hold
ITEM = "item_not_unique"  # an item that is not one particular thing
FOLDED = "folded_into_another"  # another spelling or disguise of a name kept (`why` says which)
OVER_CAP = "over_the_number"  # past the read's limit of names
PLACE_SPELLING = "place_spelling_suggested"  # an existing place spelled differently: a suggestion
PICTURE_CORRECTED = "picture_corrected"  # a picture's misspelling the document corrects
PICTURE_HELD = "picture_held"  # a name only a picture gives, held for the DM
STORE_REFUSED = "store_refused"  # the memory store would not take it (a rule; the log says which)
STORED = "stored"


def note(name: str, reason: str, why: str = "") -> None:
    """Record where `name` ended, when a trace is being collected."""
    rows = _ROWS.get()
    if rows is not None and name:
        rows.append((name, reason, why))


@contextmanager
def collecting() -> Iterator[list[Note]]:
    """Collect the notes made inside, in order."""
    rows: list[Note] = []
    token = _ROWS.set(rows)
    try:
        yield rows
    finally:
        _ROWS.reset(token)
