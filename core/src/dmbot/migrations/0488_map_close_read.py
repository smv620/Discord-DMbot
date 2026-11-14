"""0488_map_close_read

A map is read closely once, when it first has both yeses (#4349, MapBot 6b-2; Supervisor's ruling on
#4349, 2026-11-14).
"""

from __future__ import annotations

# The close read (places, sizes and how they sit) moved from the moment a picture is posted to a
# sweep that reads each kept map once DMbot may use it (`maps.store.may_use_map`). The sweep's mark
# lives on the map's row, so a restart never reads one twice and two sweeps never read one map at
# once. The two columns together say where a map is:
# - `close_read_at` NULL, `close_read_tries` 0: not read yet;
# - `close_read_at` set, tries above 0: a sweep has claimed it and is reading (a claim older than
#   the store's lease is taken again: the bot stopped mid-read);
# - `close_read_at` set, tries 0: read (the store resets the tries when a read is kept);
# - `close_read_at` NULL, tries above 0: a read failed that many times; at 3 it is left alone.
# Housekeeping, not a DM action: not in the change log's columns (`_changes.MAPS`), so Undo of a
# DM's answer never sees them. No row is changed: a map kept before this is read once by the sweep.
SQL = r"""
    ALTER TABLE campaign_maps
        ADD COLUMN close_read_at BIGINT,
        ADD COLUMN close_read_tries SMALLINT NOT NULL DEFAULT 0
            CHECK (close_read_tries BETWEEN 0 AND 3);
    """

ISOLATED_TABLES: tuple[str, ...] = ()
