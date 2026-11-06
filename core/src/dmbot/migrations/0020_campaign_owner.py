"""0020_campaign_owner."""

from __future__ import annotations

CAMPAIGN_OWNER = """
    -- The campaign's owner (#437): the subscriber whose plan's hours and campaign count
    -- it uses. Set to the creating DM, and to whoever restores a backup as a new
    -- campaign; replacing a campaign keeps its owner. NULL for campaigns from before
    -- this (no creator was recorded; #437 decides how they get one).
    ALTER TABLE campaigns ADD COLUMN owner_user_id BIGINT;
    """

SQL = CAMPAIGN_OWNER
