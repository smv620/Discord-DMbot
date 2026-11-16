"""A story thread's clues (#4624, quests slice 6a; design `docs/design/quests.md` §3, §7, §10):
the `clue_to` connection's words and the pure rules of the "clues found" count.

A clue is tied to a thread by the DM (`clue_to`, clue → thread), with one of two words: "points to
it" (the default) or "false lead". Both the tie and its word are the DM's alone (Supervisor's
condition on #4624): the connection is always stored secret, no knower or belief may ever be written
on it (`refused_knower`, called from the one write layer), and the gate leaves it out of every
player's view. A player sees only the clues their own characters know, never a count and never
the false-lead mark.

"Solved so far" is worked out when shown and never stored (§7): a count of the tied clues that a
player character (or the party) knows, with a total only while the DM has tied clues the table has
not found yet. DMbot never invents a total, never gives a percentage and never says a guess is
right."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

CLUE, CLUE_TO = "clue", "clue_to"
POINTS_TO, FALSE_LEAD = "points to it", "false lead"
WORDS = (POINTS_TO, FALSE_LEAD)
# The connection kinds the players never see in any form, nor anyone's knowledge of them
DM_ONLY_PREDICATES: frozenset[str] = frozenset({CLUE_TO})

DM_ONLY_TIE = "Which clues a story thread has is for the DM alone: players never see it."
NOT_A_CLUE_WORD = 'A clue either "points to it" or is a "false lead".'
ONLY_CLUES = "Only a clue or secret can be a clue to a story thread."
ALREADY_TIED = "That clue is already on this thread."
NOT_TIED = "That clue isn't tied to this story thread any more."


def word_of(detail: str | None) -> str:
    """The tie's word as stored: "false lead", or "points to it" for anything else."""
    return FALSE_LEAD if (detail or "").strip().lower() == FALSE_LEAD else POINTS_TO


@dataclass(frozen=True, slots=True)
class ThreadClue:
    """One clue tied to a thread, as the DM's card shows it."""

    clue_id: str
    relation_id: str
    name: str
    false_lead: bool
    found: bool  # a player character or the party knows it


def found_text(clues: Collection[ThreadClue]) -> str:
    """The count, in §7's forms; "" with no clue tied. DM only.

    A fraction only while some tied clue is unfound (the DM listed more than play found:
    Supervisor's ruling 1 on #4624); with every one found, "N clues found so far", as DMbot
    doesn't know a total."""
    if not clues:
        return ""
    total = len(clues)
    found = sum(1 for c in clues if c.found)
    leads = sum(1 for c in clues if c.false_lead)
    clue_s = "clue" if total == 1 else "clues"
    lead = f" ({leads} is a false lead)" if leads == 1 else f" ({leads} are false leads)"
    lead = lead if leads else ""
    if found == total:
        many = "clue" if found == 1 else "clues"
        return f"{found} {many} found so far{lead}."
    return f"{found} of {total} {clue_s} found{lead}. Not yet found: {total - found}."


__all__ = [
    "ALREADY_TIED",
    "CLUE",
    "CLUE_TO",
    "DM_ONLY_PREDICATES",
    "DM_ONLY_TIE",
    "FALSE_LEAD",
    "NOT_A_CLUE_WORD",
    "NOT_TIED",
    "ONLY_CLUES",
    "POINTS_TO",
    "WORDS",
    "ThreadClue",
    "found_text",
    "word_of",
]
