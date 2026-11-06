"""0015_dm_screen_level."""

from __future__ import annotations

DM_SCREEN_LEVEL = """
    -- How much DMbot says in the DM screen (#504): quiet, normal (the default) or
    -- chatty. Alerts are shown at every level.
    ALTER TABLE campaigns ADD COLUMN dm_screen_level TEXT NOT NULL DEFAULT 'normal'
        CHECK (dm_screen_level IN ('quiet', 'normal', 'chatty'));
    """

SQL = DM_SCREEN_LEVEL
