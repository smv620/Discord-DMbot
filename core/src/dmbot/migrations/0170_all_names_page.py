"""0170_all_names_page

The website's All names list (#2294).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    ALL_NAMES_PAGE,
)

SQL = ALL_NAMES_PAGE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The All names page's list (#2294): DMs, and managers without what only a DM sees.
    (
        "dmbot_web_all_names",
        "dmbot_web_all_names(BIGINT, TEXT, TEXT, TEXT, BOOLEAN, INT, INT, TEXT)",
    ),
)
