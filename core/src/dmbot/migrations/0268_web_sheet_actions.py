"""0268_web_sheet_actions

A character sheet asked for on the website (#2816).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_SHEET_ACTIONS,
)

SQL = WEB_SHEET_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ("web_sheet_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The character sheets page (#2816): what a person may see, and the queue for a link, a forget
    # and a DM's read again.
    ("dmbot_web_sheet_read", "dmbot_web_sheet_read(BIGINT, TEXT)"),
    ("dmbot_web_sheet_action_add", "dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_sheet_action_read", "dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT)"),
)
