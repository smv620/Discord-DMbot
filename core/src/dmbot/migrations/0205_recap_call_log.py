"""0205_recap_call_log

When each recap call was made, for the day's tally (#2308).
"""

from __future__ import annotations

RECAP_CALL_LOG = """
    -- When each AI call of a recap job was made (UTC seconds), kept for two days, so the campaign's
    -- daily tally counts only the last 24 hours of calls and not a waiting recap's whole lifetime
    -- (#2308). A job from before this has none: it is counted as it was (its `spent`, while it was
    -- updated in the last day). The column has a default and is added, not updated: no row changes.
    ALTER TABLE recap_jobs ADD COLUMN spent_log BIGINT[] NOT NULL DEFAULT '{}';
"""

SQL = RECAP_CALL_LOG
