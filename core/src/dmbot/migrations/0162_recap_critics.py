"""0162_recap_critics

The critics loop's saved state (#2255).
"""

from __future__ import annotations

RECAP_CRITICS = """
    -- Where a recap's critics loop is (#2255): the readings done, what the critics last asked for,
    -- the best draft so far and, when it stopped without agreement, the notes left for the DM's
    -- recap page. JSON, NULL until the loop begins. Each step is saved, so a restart never repays
    -- for one that finished. It holds the draft text and the critics' notes about it, which are
    -- the recap's own words on a table that already isolates by server and is deleted with the
    -- campaign; none of it is logged. A new nullable column changes no rows.
    ALTER TABLE recap_jobs ADD COLUMN critics JSONB;
    """

SQL = RECAP_CRITICS
