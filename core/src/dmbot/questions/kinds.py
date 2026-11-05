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

# In the order the list shows them (the ones people answer most first).
ORDER: Final[tuple[str, ...]] = (
    NAMES,
    HEARD,
    LOOKALIKES,
    CONNECTIONS,
    WHICH,
    FAMILY,
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
