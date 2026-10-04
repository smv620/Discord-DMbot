"""A campaign's DM screen: a channel for the DM's private notes, with per-campaign
visibility (private / opt-in peek / open). See docs/PLAN.md, "DM-screen visibility".

`ensure_dm_screen()` is the hook `/dmbot start` calls (#48).
"""

from dmbot.dm_screen.buttons import (
    HideButton,
    PeekButton,
    VisibilityButton,
    card_view,
    hide_view,
    peek_view,
)
from dmbot.dm_screen.channel import DMScreenError, ScreenResult, ensure_dm_screen, setup_dm_screen

__all__ = [
    "DMScreenError",
    "HideButton",
    "PeekButton",
    "ScreenResult",
    "VisibilityButton",
    "card_view",
    "ensure_dm_screen",
    "hide_view",
    "peek_view",
    "setup_dm_screen",
]
