"""0035_rules_cards."""

from __future__ import annotations

RULES_CARDS = """
    -- A campaign setting (#931): "Rules cards when a spell or creature is named", off by
    -- default. When on, a live session puts a card for a spell, condition or creature on
    -- the DM screen when it is named at the table. Only the campaign's DMs change it. It
    -- travels in a backup with the campaign's other settings.
    ALTER TABLE campaigns ADD COLUMN rules_cards BOOLEAN NOT NULL DEFAULT FALSE;
"""

SQL = RULES_CARDS
