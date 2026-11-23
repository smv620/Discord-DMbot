"""The kinds of question DMbot waits on the DM for, with the words both the Discord list and the
website's Questions page use (#3435, #3438). The keys are what `question_counts` stores and the
website reads; the labels are pinned by a test and must equal `web/src/content/questions-words.ts`.
Counts and fixed words only: never a name or a question's text."""

from __future__ import annotations

from typing import Final

NAMES: Final = "names"
HEARD: Final = "heard"
LOOKALIKES: Final = "lookalikes"
CONNECTIONS: Final = "connections"
WHICH: Final = "which"
FAMILY: Final = "family"
CLAIMS: Final = "claims"
READINGS: Final = "readings"
PLOT: Final = "plot"
RECAP_APPROVE: Final = "recap_approve"
RULES: Final = "rules"
MAPS: Final = "maps"  # a map a player kept, waiting for a DM's yes (#4344)
TRAITS: Final = "traits"  # a lineage or class the lore gives a character the DM has (#3926)
WORLD: Final = "world_notes"  # DMbot's guesses about places and languages, to check (#4285)
THREADS: Final = "threads"  # story threads the lore holds, to keep or turn down (#4453)
SECRETS: Final = "secrets"  # what the players know that sounded like a secret (#4524)
JOINS: Final = "party_joins"  # characters who joined: what do they know of before? (#4537)
LEAVES: Final = "party_leaves"  # a goodbye or a death: still in the party? (#4537 slice 6)
REVEALS: Final = "reveals"  # what the characters learned, waiting to be kept (#3863, #2996)
DEEDS: Final = (
    "deeds"  # deeds the party did that mattered to someone, waiting to be recorded (#3591, #3590)
)
HEADS_UPS: Final = "heads_ups"  # continuity heads-ups waiting for the DM (#3695, #5147)

# In the order the list shows them (the ones people answer most first).
ORDER: Final[tuple[str, ...]] = (
    NAMES,
    HEARD,
    LOOKALIKES,
    CONNECTIONS,
    WHICH,
    FAMILY,
    TRAITS,
    WORLD,
    THREADS,
    SECRETS,
    JOINS,
    LEAVES,
    DEEDS,
    HEADS_UPS,
    REVEALS,
    MAPS,
    CLAIMS,
    READINGS,
    PLOT,
    RECAP_APPROVE,
    RULES,
)

LABELS: Final[dict[str, str]] = {
    NAMES: "New names to check",
    HEARD: "Did they mean…?",
    LOOKALIKES: "Names that look alike",
    CONNECTIONS: "Connections waiting",
    WHICH: "Which one?",
    FAMILY: "Family ties",
    TRAITS: "Character details",
    WORLD: "World notes",
    THREADS: "Story threads to check",
    SECRETS: "Known or secret?",
    JOINS: "New in the party",
    LEAVES: "Still in the party?",
    DEEDS: "Deeds to record",
    HEADS_UPS: "Heads-ups",
    REVEALS: "Things the characters learned",
    MAPS: "Maps waiting for your yes",
    CLAIMS: "Facts to check",
    READINGS: "What people believe",
    PLOT: "Plot check",
    RECAP_APPROVE: "Recaps to approve",
    RULES: "House rule updates",
}

# The kinds the DM is told about and counted in the total: every one has a screen that answers it
# (Family ties has the 👪 card since #3463). A kind with no screen would be counted and stored but
# left out of this tuple, so it could not start or keep a reminder going.
SHOWN: Final[tuple[str, ...]] = ORDER

# Kinds whose questions also leave by themselves (they expire), so an answer is counted from the
# answers given (`settled`) and never from the open count going down.
EXPIRING: Final[frozenset[str]] = frozenset({HEARD})
