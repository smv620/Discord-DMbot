"""0130_initiative_style_backfill

Old switch folded in (#1791).
"""

from __future__ import annotations

INITIATIVE_STYLE_BACKFILL = """
    -- The retired side-initiative switch (#1127, #1769) said the table plays by side, which is
    -- what the kept initiative style says (#1784). Fold it in, so a DM who turned it on is not
    -- asked to say it again (#1791). A style already kept is left alone. The column stays.
    -- Migrations run with no server set, so row-level security hides every row: this opens
    -- the one table it reads and writes for itself (CLAUDE.md), then closes it again.
    CREATE POLICY migrate_backfill ON campaigns FOR ALL USING (true) WITH CHECK (true);
    UPDATE campaigns SET initiative_style = 'side'
        WHERE side_initiative AND initiative_style IS NULL;
    DROP POLICY migrate_backfill ON campaigns;
"""

SQL = INITIATIVE_STYLE_BACKFILL
