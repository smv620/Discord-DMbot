"""How a waiting name was found, in words (#5133 slice 3c, #5144): the heard line and the list's
order, with no Discord in them so the website's name card says exactly what Discord's does.

`heard_line` and the order are Supervisor's ruling on #5133 (design section 6); `ui/names.py`
wraps them with its own markdown, the website sends them as plain text. Counts and spellings only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from dmbot.memory.models import BACKGROUND_SOURCE, NPC, PLAYER_CHARACTER

HEARD_AS_SHOWN = 3  # other spellings named on the card; the rest are "and N more" (#5133)
SURE_HOWS = ("introduced", "typed")  # found by a name said out loud or typed: asked first
SPELLED_OUT = "Spelled out at the table"  # a suggestion's note when someone spelled it (#2168)


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def heard_line(
    times: int,
    typed: int,
    also: Sequence[str],
    *,
    written: bool = False,
    mark: Callable[[str], str] = str,
) -> str:
    """ "Heard 5 times, sometimes as Rothgar or hroth gar.", "Heard once.", "Typed once in chat, and
    heard 3 times, sometimes as Rothgar.", or for the DM's own text "Found 2 times in your text, …".
    At most three other spellings are named, then "and N more". `mark` dresses each spelling
    (Discord italicises it)."""
    shown = [mark(a) for a in also[:HEARD_AS_SHOWN]]
    named = ""
    if shown:
        named = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + f" or {shown[-1]}"
    rest = len(also) - len(shown)
    spelled = f", sometimes as {named}" + (f" and {rest} more" if rest > 0 else "") if also else ""
    times = max(times, 1)
    typed = min(max(typed, 0), times)
    if written:
        return f"Found {_times(times)} in your text{spelled}."
    if not typed:
        return f"Heard {_times(times)}{spelled}."
    # (heard: the hearings that weren't typed; "sometimes as", since which spelling each one was
    # isn't kept, so "heard 3 times as Rothgar" could say more than DMbot knows)
    heard = times - typed
    also_heard = f", and heard {_times(heard)}" if heard else ""
    return f"Typed {_times(typed)} in chat{also_heard}{spelled}."


def heard_times(description: str, found_times: int | None = None) -> int:
    """How many times a waiting name was heard: the count kept with what made it a name (0611),
    else the one the after-session step wrote in its note ("Heard 5 times"), else 1."""
    if found_times is not None and found_times > 0:
        return found_times
    said = re.search(r"\b(\d+)\b", description)
    return max(int(said.group(1)), 1) if said else 1


def surest(how: str | None, times: int, created_at: int, name: str) -> tuple[int, int, int, str]:
    """The waiting list's order: names with no found row last, then introduced or typed first, then
    the most heard, then as made (`times` is `heard_times`'s answer)."""
    if how is None:
        return (2, 0, created_at, name)
    return (0 if how in SURE_HOWS else 1, -times, created_at, name)


def guess_kind(
    source: str, type_: str, role: str | None, *, extra_sources: tuple[str, ...] = ()
) -> str:
    """The kind a suggestion was given when it was read (npc, place…), or "" when it has none to
    confirm as (an idea, or nothing guessed): from the DM's background (#2167) or the name scan
    (#2937). `extra_sources` adds readers whose kind the card may start on but that nothing is
    done automatically from (`card_guess`); `ui/names.background_kind`, which auto-accept also
    asks, keeps just the two."""
    if source not in (BACKGROUND_SOURCE, "scan", *extra_sources):  # marked by where it came from
        return ""
    if type_ == "character":
        # (read from a text that says so, #3553; confirmed with its player)
        return {NPC: NPC, PLAYER_CHARACTER: PLAYER_CHARACTER}.get(role or "", "")
    return type_ if type_ in ("place", "faction", "item", "event") else ""


def and_list(items: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def kind_label(kind: str) -> str:
    """ "person", "place"…: a kind in plain words (the same ones the background reader uses)."""
    from dmbot.memory.background import LABELS

    return LABELS.get(kind, (kind, kind))[0]


def a_kind(kind: str) -> str:
    """ "a place", "an item": the kind with its article."""
    word = kind_label(kind)
    an = word.casefold().startswith(("a", "e", "i", "o", "u", "npc"))  # ("an NPC": "en-pee-see")
    return f"{'an' if an else 'a'} {word}"


def rules_note(
    name: str,
    kind: str,
    rules: Sequence[str],
    fixed: str,
    *,
    bold: Callable[[str], str] = str,
    italic: Callable[[str], str] = str,
    plain: Callable[[str], str] = str,
) -> str:
    """The reply to the new card's yes (#5133 slice 3c-2b, as ruled): what was kept, and which
    heard spellings now read as it. `fixed`: "fixed" (this session's transcript too), "later"
    (from now on only: an entry not everyone at the table may read about), "fixing" (the wait
    line) or "failed". Discord dresses names in markdown (`bold`, `italic`, `plain`); the website
    sends plain text."""
    if fixed == "failed":
        return (
            f"Kept {bold(name)}, but this session's transcript couldn't be fixed. "
            "Press 🔁 Try again."
        )
    head = f"✅ Kept {bold(name)} as {a_kind(kind)}"
    if fixed == "fixing":
        return f"{head}, and DMbot is fixing this session's transcript."
    if not rules:
        return f"{head}."
    verb = "reads" if len(rules) == 1 else "read"
    when = "in this session's transcript and from now on" if fixed == "fixed" else "from now on"
    spelled = and_list([italic(r) for r in rules])
    return f"{head}. {spelled} now {verb} as {plain(name)}, {when}."


def card_guess(source: str, type_: str, role: str | None) -> str:
    """The kind a name's card starts on (#5218): `guess_kind`, and the story reader's too, which
    auto-accept does not act on. Discord's card and the website's both use it, so the one tap on
    Yes means the same in both."""
    return guess_kind(source, type_, role, extra_sources=("extractor",))
