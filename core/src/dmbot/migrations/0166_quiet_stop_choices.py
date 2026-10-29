"""0166_quiet_stop_choices

No Off, 5/10/15/30, default 10 (#2288).
"""

from __future__ import annotations

QUIET_STOP_CHOICES_SQL = """
    -- Stop when quiet has no Off and offers 5, 10, 15 or 30 minutes, 10 by default (#2288).
    -- A campaign on Off, 60 or 90 goes to 30, the longest wait left; one on 30 stays on 30.
    -- This changes rows, and every campaign table has forced row-level security, so the update
    -- opens the table for itself and closes it again (migrations run with no server set).
    ALTER TABLE campaigns DROP CONSTRAINT campaigns_quiet_stop_minutes_check;
    CREATE POLICY migrate_backfill ON campaigns USING (true) WITH CHECK (true);
    UPDATE campaigns SET quiet_stop_minutes = 30 WHERE quiet_stop_minutes NOT IN (5, 10, 15, 30);
    DROP POLICY migrate_backfill ON campaigns;
    ALTER TABLE campaigns ALTER COLUMN quiet_stop_minutes SET DEFAULT 10;
    ALTER TABLE campaigns
        ADD CONSTRAINT campaigns_quiet_stop_minutes_check
            CHECK (quiet_stop_minutes IN (5, 10, 15, 30));
"""

SQL = QUIET_STOP_CHOICES_SQL
