"""0213_handover_claim

Opening a hand-over link on the website (#2532).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    HANDOVER_CLAIM,
)

SQL = HANDOVER_CLAIM

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_handover_open", "dmbot_handover_open(BIGINT, BIGINT, TEXT, TEXT, BOOLEAN)"),  # #2532
    ("dmbot_handover_release", "dmbot_handover_release(BIGINT, BIGINT, TEXT)"),  # #2532
    ("dmbot_handover_decline", "dmbot_handover_decline(BIGINT, BIGINT, TEXT, TEXT)"),  # #2532
)
