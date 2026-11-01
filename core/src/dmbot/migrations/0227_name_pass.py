"""0227_name_pass

PlotBot repairs names in a finished transcript (#2628).
"""

from __future__ import annotations

NAME_PASS = """
    -- PlotBot's name pass on a finished session's cleaned transcript (#2628): when it was done
    -- (Unix seconds), 0 when it has not been. The recap of a session waits for it (a bounded wait,
    -- in code). Every session that exists now is marked done, so nothing old is asked again and no
    -- old recap waits. A time only, no words.
    ALTER TABLE transcript_sessions ADD COLUMN names_repaired_at BIGINT NOT NULL DEFAULT 0;
    -- Migrations run with no server set, so row-level security hides every row: open the table to
    -- this one UPDATE, dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON transcript_sessions
        FOR ALL USING (true) WITH CHECK (true);
    UPDATE transcript_sessions SET names_repaired_at = GREATEST(COALESCE(ended_at, started_at), 1);
    DROP POLICY migrate_backfill ON transcript_sessions;
"""

SQL = NAME_PASS
