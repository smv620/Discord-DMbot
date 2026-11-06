"""0084_recap_switches

The DM's recap choices (#1421).
"""

from __future__ import annotations

RECAP_SWITCHES = """
    -- The DM's recap choices on the campaign (#1421): write a recap after each session, make
    -- the narration and music, email them when it is ready. All on by default; they can only
    -- turn things off, never on past what the plan allows. They travel in a backup with the
    -- campaign's other settings.
    ALTER TABLE campaigns ADD COLUMN recaps_text BOOLEAN NOT NULL DEFAULT TRUE;
    ALTER TABLE campaigns ADD COLUMN recaps_audio BOOLEAN NOT NULL DEFAULT TRUE;
    ALTER TABLE campaigns ADD COLUMN recaps_email BOOLEAN NOT NULL DEFAULT TRUE;
    """

SQL = RECAP_SWITCHES
