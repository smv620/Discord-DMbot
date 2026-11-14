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

import re
from collections import Counter
from collections.abc import Collection, Sequence

from dmbot.memory import descriptions
from dmbot.memory.background import Connection, Found
from dmbot.memory.end_match import Ends
from dmbot.memory.models import name_key
from dmbot.memory.pronouns import PRONOUNS
from dmbot.names import not_names

MAX_PC_NAMES = 20
MAX_WORDS = 4  # a name of more words is a phrase
# Only a tie between two people can show that an unlisted end is a person: a place, an item, a
# group or a god at the end of owns, located_in, member_of, contains, leads or founded is not.
PERSON_TIES = frozenset(
    {
        "kin_of", "knows", "ally_of", "enemy_of", "serves", "sworn_to", "mentor_of", "rival_of",
        "captive_of",
    }
)  # fmt: skip
_PHRASE = re.compile(r"[,;:()\[\]0-9]|['’]s\b|['’]$")  # a possessive, a comma, a bracket, a digit


def _looks_like_a_name(text: str) -> bool:
    text = text.strip()
    words = text.split()
    return bool(
        text
        and len(words) <= MAX_WORDS
        and len(text) <= 100
        and text[0].isupper()
        and not _PHRASE.search(text)
        and words[0].casefold() not in PRONOUNS
        and not descriptions.is_description(text)
        and not_names.allowed(text, frozenset())
        and not_names.why_not(text) is None
    )


def pc_new_names(
    links: Sequence[Connection],
    names: Sequence[Found],
    known: Collection[str],
    players: Collection[str],
    counts: Counter[str],
    *,
    spellings: Collection[str] = (),
) -> list[Found]:
    """The names to add: an end of a person-to-person connection that no name, spelling or known
    key holds (nor fits by its first or last word, as `keep_connections` reads an end), whose other
    end is a player character. `spellings`: every other spelling of a name this read or the
    campaign has, secret ones too (a disguise is never added as a name of its own). Counted in
    `counts` as `pc_name_made` and `pc_name_over_cap`."""
    have = {name_key(k) for k in known} | {name_key(k) for k in spellings}
    for f in names:
        have |= {name_key(f.name), *(name_key(x) for x in (*f.also, *f.secret))}
    reader = Ends({k: k for k in have}, sorted(have))
    mine = {name_key(p) for p in players}
    out: dict[str, Found] = {}
    for link in links:
        if link.how not in PERSON_TIES:
            continue
        for unknown, other in ((link.a, link.b), (link.b, link.a)):
            key = name_key(unknown)
            if (
                key in have
                or key in out
                or name_key(other) not in mine
                or not _looks_like_a_name(unknown)
                or (matched := reader.match(unknown)).name is not None
                or matched.ambiguous
            ):
                continue
            if len(out) >= MAX_PC_NAMES:
                counts["pc_name_over_cap"] += 1
                continue
            out[key] = Found(unknown.strip(), "npc", (), (), link.quote)
            counts["pc_name_made"] += 1
    return list(out.values())
