"""0488_map_close_read

A map is read closely once, when it first has both yeses (#4349, MapBot 6b-2; Supervisor's ruling on
#4349, 2026-11-14).
"""

from __future__ import annotations

# The close read (places, sizes and how they sit) moved from the moment a picture is posted to a
# sweep that reads each kept map once DMbot may use it (`maps.store.may_use_map`). The sweep's mark
# lives on the map's row, so a restart never reads one twice and two sweeps never read one map at
# once. The two columns together say where a map is:
# - `close_read_at` NULL, `close_read_tries` 0: not read yet (or a put-off read gave its try back);
# - `close_read_at` set, tries 1 or 2: claimed and being read, or failed that many times; taken
#   again once it is older than the store's lease (the bot stopped mid-read, or the wait between
#   tries);
# - `close_read_at` set, tries 3: failed (or stopped) three times: left alone, counted in the log;
# - `close_read_at` set, tries 0: read (the store resets the tries when a read is kept).
# The 3 is `maps.store.CLOSE_READ_TRIES`. Housekeeping, not a DM action: not in the change log's
# columns (`_changes.MAPS`), so Undo of a DM's answer never sees them. No row is changed: a map
# kept before this is read once by the sweep, unless its note already has a close read.
#
# A map note names its picture (`map_notes.picture_id`), so each map of a message with several
# gets its own close read: the posting path fills it, the sweep matches on it. An older note has
# none, and the sweep takes it only when it is its message's one note. Not in a backup (the
# pictures are not yet either); a deleted picture leaves its note with no picture (`SET NULL` on
# the one column, so `guild_id` stays), and a campaign moved to another server carries it along.
SQL = r"""
    ALTER TABLE campaign_maps
        ADD COLUMN close_read_at BIGINT,
        ADD COLUMN close_read_tries SMALLINT NOT NULL DEFAULT 0
            CHECK (close_read_tries BETWEEN 0 AND 3);

    ALTER TABLE map_notes
        ADD COLUMN picture_id BIGINT,
        ADD FOREIGN KEY (picture_id, guild_id, campaign_id)
            REFERENCES campaign_pictures (id, guild_id, campaign_id)
            ON DELETE SET NULL (picture_id) ON UPDATE CASCADE;
    """

ISOLATED_TABLES: tuple[str, ...] = ()
