"""0177_web_relationships

The relationship web's data (#2323).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_RELATIONSHIPS,
)

SQL = WEB_RELATIONSHIPS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The relationship web's data (#2323).
    (
        "dmbot_web_relationships",
        "dmbot_web_relationships(BIGINT, TEXT, TEXT, INT, INT, BOOLEAN)",
    ),
)
