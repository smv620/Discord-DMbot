"""A name the lore ties to a player character but its name list lacks becomes a name to check
(#3238, drop 2). Pure.

"Cerric's brother Ulric left home" ties Ulric to the player character Cerric, but if pass 1 never
listed Ulric the connection was dropped (`name_not_found`) and the DM's own character kept no
family. The family of the table's characters is what a DM most wants kept, so such an end is added
to the read's names, ahead of the read's own name limit, as a new name for the DM to check (never
confirmed by DMbot). Only a proper name qualifies: a described person ("the sister") is drop 3's,
and a word that is no name is left out. At most `MAX_PC_NAMES` a read; the rest are counted.
Counts only."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence

from dmbot.memory import descriptions
from dmbot.memory.background import Connection, Found
from dmbot.memory.models import name_key
from dmbot.names import not_names

MAX_PC_NAMES = 20
MAX_WORDS = 4  # a name of more words is a phrase


def _looks_like_a_name(text: str) -> bool:
    text = text.strip()
    words = text.split()
    return bool(
        text
        and len(words) <= MAX_WORDS
        and len(text) <= 100
        and text[0].isupper()
        and not descriptions.is_description(text)
        and not_names.allowed(text, frozenset())
        and not_names.why_not(text) is None
    )


def pc_new_names(
    links: Sequence[Connection],
    names: Sequence[Found],
    known: Collection[str],
    players: Collection[str],
    dropped: Counter[str],
) -> list[Found]:
    """The names to add: an end of a connection that no name or known key holds, whose other end is
    a player character. Counted in `dropped` as `pc_name_made` and `pc_name_over_cap`."""
    have = {name_key(k) for k in known}
    for f in names:
        have |= {name_key(f.name), *(name_key(x) for x in (*f.also, *f.secret))}
    mine = {name_key(p) for p in players}
    out: dict[str, Found] = {}
    for link in links:
        for unknown, other in ((link.a, link.b), (link.b, link.a)):
            key = name_key(unknown)
            if (
                key in have
                or key in out
                or name_key(other) not in mine
                or not _looks_like_a_name(unknown)
            ):
                continue
            if len(out) >= MAX_PC_NAMES:
                dropped["pc_name_over_cap"] += 1
                continue
            out[key] = Found(unknown.strip(), "npc", (), (), link.quote)
            dropped["pc_name_made"] += 1
    return list(out.values())
