"""0145_recap_respelled

A recap's text followed a fixed spelling (#2170).
"""

from __future__ import annotations

RECAP_RESPELLED = """
    -- When a fixed name's spelling changed a recap's text (#2170, part C): the site shows the DM a
    -- small "spelling updated" note on it, most of all on a posted recap, whose Discord message is
    -- not changed. NULL: never. A new nullable column: no rows change, so no backfill policy.
    ALTER TABLE recap_jobs ADD COLUMN respelled_at BIGINT;
    """

SQL = RECAP_RESPELLED
