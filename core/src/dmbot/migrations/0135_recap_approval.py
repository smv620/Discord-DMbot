"""0135_recap_approval

Approve the text, then pick the audio (#2083).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    RECAP_APPROVAL,
)

SQL = RECAP_APPROVAL

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Approving a recap's text and picking its audio (#2083): the only write to those columns.
    ("dmbot_recap_approve", "dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    (
        "dmbot_recap_check_names",
        "dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT)",
    ),  # its secret check
)
