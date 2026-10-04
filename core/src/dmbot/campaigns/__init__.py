"""Campaigns: separate, persistent memory for each game a Discord server runs.

A Discord server can run several campaigns. Each one has its own rulesets, optional-rule
settings, DMs, DM screen channel, and, as features land, its own house rules, name list,
NPCs, game clock, and transcripts. Nothing is ever shared between campaigns, and no
campaign is visible to any other Discord server (see CLAUDE.md, "Campaign and server
isolation").
"""

from dmbot.campaigns.models import (
    FALLBACK_NONE,
    RULESETS,
    Campaign,
    CampaignError,
)
from dmbot.campaigns.store import CampaignStore, ExportSection

__all__ = [
    "FALLBACK_NONE",
    "RULESETS",
    "Campaign",
    "CampaignError",
    "CampaignStore",
    "ExportSection",
]
