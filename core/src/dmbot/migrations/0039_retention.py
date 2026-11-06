"""0039_retention."""

from __future__ import annotations

RETENTION = """
    -- Which retention warning a campaign has had (#964), so a restart doesn't repeat one:
    -- the stage (0 none, 1 the 14-day warning, 2 the 3-day one) and the delete date it was
    -- sent for. Playing a session moves the date, so the warnings start over. Columns with
    -- defaults: no rows change, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN retention_warned_stage INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE campaigns ADD COLUMN retention_warned_for BIGINT;
"""

SQL = RETENTION
