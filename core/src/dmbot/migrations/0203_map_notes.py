"""0203_map_notes

A map put into words, kept for MapBot (#2450).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

MAP_NOTES = """
    -- A map someone posted in the table chat, put into words for MapBot, a later feature (#2450,
    -- docs/PLAN.md, "Images posted there"): the places on it and where each is from the others, and
    -- the layout as a whole. Words only, never the picture. `user_id` is who posted it, so the
    -- notes go when that person deletes their lines. Rows go with their campaign; a campaign keeps
    -- at most 200 (the store drops the oldest). New table: no rows change, so no backfill policy.
    CREATE TABLE map_notes (
        id          BIGSERIAL PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        -- the Discord message the map was posted in, so deleting the picture deletes its note
        -- (0: restored from a backup, which never carries message IDs)
        message_id  BIGINT NOT NULL DEFAULT 0,
        made_at     BIGINT NOT NULL,
        alt         TEXT NOT NULL CHECK (char_length(alt) BETWEEN 1 AND 400),
        layout      TEXT NOT NULL CHECK (char_length(layout) <= 2500),
        places      JSONB NOT NULL CHECK (jsonb_typeof(places) = 'array'),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX map_notes_campaign ON map_notes (campaign_id, id);
    CREATE INDEX map_notes_person ON map_notes (guild_id, user_id);
    CREATE INDEX map_notes_message ON map_notes (guild_id, message_id) WHERE message_id <> 0;
    """ + _isolate("map_notes")

SQL = MAP_NOTES

ISOLATED_TABLES: tuple[str, ...] = ("map_notes",)
