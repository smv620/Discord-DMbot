"""0233_web_waiting

The website reads and answers what waits (#2652).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_WAITING,
)

SQL = WEB_WAITING

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # What waits for the DM: claims, a backstory's connections and "Which Mara?" questions (#2652).
    ("dmbot_web_waiting", "dmbot_web_waiting(BIGINT, TEXT)"),
)
