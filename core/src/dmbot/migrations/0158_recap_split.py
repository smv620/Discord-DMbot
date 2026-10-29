"""0158_recap_split

A session's opening split into parts (#2253).
"""

from __future__ import annotations

RECAP_SPLIT = """
    -- How a recap's session was split (#2253): its opening into talk, catch-up and last time's
    -- recap, then play. NULL until the split stage has run (or for a job from before it). Ranges
    -- of line numbers and a flag only, never a word of the transcript, so nothing here needs
    -- consent or row-level changes: the column is on a table that already isolates by server. A
    -- new nullable column changes no rows. The next session reads it to leave this session's
    -- retelling out of what it compares with.
    ALTER TABLE recap_jobs ADD COLUMN split JSONB;
    """

SQL = RECAP_SPLIT
