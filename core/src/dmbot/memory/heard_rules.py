"""Which heard spellings of a name the DM kept may become "change to" rules (#5133 slice 3c-2b;
docs/design/session_names.md, section 6, as Supervisor ruled).

When the DM says yes to a waiting name, each other spelling it was heard as becomes a rule for the
Transcript Cleaner ("rothgar" is written "Hrothgar"), so it is written as the name from then on and
never sent to speech-to-text as a hint. A rule rewrites those words everywhere in the campaign, so
some spellings must never become one (the MUST guard):
- an ordinary word: one of the scan's common words, anything `not_names` says is no name, or a
  spelling every word of which is also said in lower case in the session (the #5121 test, with the
  spelling's own sightings left out);
- one or two letters;
- another known name, or words that already have a rule (checked in the store, in the same change).

Pure: no store, no Discord. The session's lines are read for the word test only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dmbot.memory.models import name_key
from dmbot.memory.scan import COMMON, GAME_TERMS, ordinary_in
from dmbot.names import not_names

MIN_LETTERS = 3  # a rule for "Al" or "Ka" would rewrite too much


def only_capitals(name: str, spelling: str) -> bool:
    """A one-word spelling that differs from the name only by capitals ("hope" for an NPC called
    "Hope") makes no rule (Supervisor, #5133): it would rewrite every "I hope", and the common
    words and the lower-case test can't tell, since the name's own sightings are left out."""
    return len(spelling.split()) == 1 and spelling.casefold() == name.casefold()


def too_short(spelling: str) -> bool:
    return sum(c.isalnum() for c in spelling) < MIN_LETTERS


def ordinary(spelling: str, lines: Iterable[str]) -> bool:
    """An ordinary word, never a rule: common, not a name, or said in lower case this session
    outside its own sightings."""
    key = name_key(spelling)
    if not key or key in COMMON or key in GAME_TERMS:
        return True
    if not_names.is_not_name(spelling) or not_names.shape_of(spelling) is not None:
        return True
    return ordinary_in(spelling, lines, leaving_out=[spelling])


def may_be_rules(name: str, spellings: Sequence[str], lines: Sequence[str]) -> list[str]:
    """The spellings that pass the pure guard, in the order given: not the name itself (by key),
    not too short, not ordinary. The store adds the checks that need the campaign's names."""
    own = name_key(name)
    out: list[str] = []
    seen: set[str] = set()
    for spelling in spellings:
        key = name_key(spelling)
        if not key or key == own or key in seen or only_capitals(name, spelling):
            continue
        seen.add(key)
        if too_short(spelling) or ordinary(spelling, lines):
            continue
        out.append(spelling)
    return out
