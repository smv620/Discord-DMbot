"""0468_waiting_route_means

The waiting connections' key includes a route's means (#4173 slice 6d part 2), so a road and a
river between the same two places are two offers.
"""

from __future__ import annotations

# Supervisor's ruling on #4173 (2026-11-13 09:39): the key is subject, kind, object and means, the
# means '' for every kind but `route_to` (whose detail is its means, `memory/route_words.py`), so
# every other kind keys exactly as before. A stored generated column: no row is written to fill it
# (Postgres computes it as the table is rewritten), so no row-level security needs opening, and no
# insert names it. Every key lookup in `memory/backstory_links.py` adds it. Not in a backup (the
# waiting list is a wait, `campaigns/tables.py`).
SQL = r"""
    ALTER TABLE backstory_links
        ADD COLUMN means TEXT GENERATED ALWAYS AS (
            CASE WHEN predicate = 'route_to' THEN detail ELSE '' END
        ) STORED;
    ALTER TABLE backstory_links DROP CONSTRAINT backstory_links_pkey;
    ALTER TABLE backstory_links ADD CONSTRAINT backstory_links_pkey
        PRIMARY KEY (guild_id, campaign_id, subject_id, predicate, object_id, means);
"""
