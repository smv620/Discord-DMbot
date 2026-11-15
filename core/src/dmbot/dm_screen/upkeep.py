"""Where an upkeep note goes (#3874): the DM Bot channel, else the DM screen as before.

The DM screen is for live play only; setup and settings notes belong in the DM Bot channel. That
channel is readable by the campaign's people, so such a note holds counts, kinds and rule names
only, never a character's or story name. One function so every note picks its channel the same
way (`DMBot._upkeep_channel` gives the same answer for the recap notes).
"""

from __future__ import annotations

from dmbot.campaigns import Campaign


def channel_for(campaign: Campaign) -> int | None:
    """The channel for an upkeep note, or None when the campaign has neither."""
    return campaign.dm_bot_channel_id or campaign.dm_screen_channel_id
