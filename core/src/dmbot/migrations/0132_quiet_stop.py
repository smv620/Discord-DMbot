"""0132_quiet_stop

Minutes of silence before an automatic stop (#2034).
"""

from __future__ import annotations

QUIET_STOP = """
    -- Minutes with nobody speaking before DMbot stops a session by itself (#2034): 30, 60 or 90,
    -- or 0 for off. A defaulted column changes no rows, so no row-level-security policy is
    -- needed.
    ALTER TABLE campaigns
        ADD COLUMN quiet_stop_minutes INTEGER NOT NULL DEFAULT 30
            CHECK (quiet_stop_minutes IN (0, 30, 60, 90));
"""

SQL = QUIET_STOP
