"""The "who is who at the table" lines for the sidebar's message (#1188): names only, no
Discord IDs. Pure, so the rules (who is left out, how a name is cleaned) are unit-tested.
Names are typed by people (nicknames, characters, names learned from speech), so each is
cleaned before it goes into a prompt: one line, no control characters, short."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Collection, Iterable

NAME_MAX = 40


def clean_name(text: str) -> str:
    """One short plain line: control characters and line breaks become spaces."""
    flat = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    return " ".join(flat.split())[:NAME_MAX].strip()


def lines(
    rows: Iterable[tuple[int, list[str], list[str]]],
    agreed: Collection[int],
    dm_ids: Collection[int],
    name_of: Callable[[int], str],
    dm_word_user: int | None = None,
) -> list[str]:
    """ "Mia plays Cerric, is also called Mike" for each person who still agrees to be
    recorded and has a name to show. A person with two entries is one line."""
    merged: dict[int, tuple[list[str], list[str]]] = {}
    for user_id, characters, also in rows:
        if user_id not in agreed:
            continue
        have = merged.setdefault(user_id, ([], []))
        have[0].extend(c for c in characters if c not in have[0])
        have[1].extend(a for a in also if a not in have[1])
    out: list[str] = []
    # "The DM", "the GM"... mean the DM who started the session (the starter, when there are
    # co-DMs) for whoever asks; these words are never stored as anyone's name (#1208).
    if dm_word_user is not None and dm_word_user in agreed:
        dm_name = name_of(dm_word_user)
        if not dm_name.startswith("<@") and clean_name(dm_name):
            out.append(f"DM, GM, Dungeon Master and Game Master all mean {clean_name(dm_name)}")
    for user_id, (characters, also) in merged.items():
        name = name_of(user_id)
        if name.startswith("<@") or not clean_name(name):
            continue  # no name to show
        parts = []
        if user_id in dm_ids:
            parts.append("is the DM")
        shown = [c for c in map(clean_name, characters) if c]
        if shown:
            parts.append("plays " + " and ".join(shown))
        called = [a for a in map(clean_name, also) if a]
        if called:
            parts.append("is also called " + " and ".join(called))
        if parts:
            out.append(f"{clean_name(name)} " + ", ".join(parts))
    return out
