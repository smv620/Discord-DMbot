"""0211_plot_chat

The DM's talk with PlotBot about an issue (#2123).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    PLOT_CHAT,
)

SQL = PLOT_CHAT

ISOLATED_TABLES: tuple[str, ...] = ("plot_chat",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The DM's talk with PlotBot about an issue, and Save / Not that on its proposals (#2123).
    ("dmbot_plot_chat_send", "dmbot_plot_chat_send(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    ("dmbot_plot_chat_read", "dmbot_plot_chat_read(BIGINT, TEXT, BIGINT, TEXT)"),
    ("dmbot_plot_proposal", "dmbot_plot_proposal(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)"),
)
