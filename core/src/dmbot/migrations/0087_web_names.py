"""0087_web_names

The website's doors to the names and their spelling (#1451).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_NAMES,
)

SQL = WEB_NAMES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The campaign's names with the narrator's spelling (#1451): read and write, DM only.
    ("dmbot_web_names", "dmbot_web_names(BIGINT, TEXT)"),
    ("dmbot_web_set_said_as", "dmbot_web_set_said_as(BIGINT, TEXT, TEXT, TEXT)"),
)
