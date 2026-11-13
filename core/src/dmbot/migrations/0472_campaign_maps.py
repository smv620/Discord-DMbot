"""0472_campaign_maps

Maps are kept as maps (#4344, MapBot layers slice 3a; docs/design/mapbot_layers.md sections 3.4, 8.1
and rule 3 of section 13).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row is one map: a kept picture (`campaign_pictures`) that DMbot knows is a map. The row exists
# only after its uploader's right-to-use yes (`right_by`, `right_at`: NOT NULL, so nothing is kept
# as a map without it, rule 3); the DM's yes to using it is `use_ok` (NULL waiting, true yes, false
# no) with who said it and when. A DM's own upload fills both at once. One function in
# `maps/store.py` (`may_use_map`) answers "may DMbot use this map?"; every reader calls it.
#
# `audience` is 'dm' or 'table' (section 8.1) and 'table' only for a map that came from the table's
# chat (`came_from = 'discord'`): "Show to the table" is a later, explicit change of the DM's.
# `scale`, `grid` and `north` carry where each came from (read, dm, import, or assumed), the
# measuring comes in slice 4. The vocabularies are fixed lists in code with `other` (rule 6).
#
# The picture is tied by a composite key that includes the campaign, so a map can never point at
# another campaign's picture: the unique key on `campaign_pictures` below exists for that. Deleting
# the picture (soft or hard) removes its map in `PictureStore.delete`, and a hard delete or the
# campaign cascades. Every key that carries `guild_id` follows a campaign moved to another server
# (`ON UPDATE CASCADE`, #2550). The place a map covers and the older map it replaces are kept loose
# (`SET NULL` on the one column, so the row stays).
#
# `memory_map_facts.map_id` is the pointer slice 2 left for this slice (its migration 0457 says so):
# nullable, `ON DELETE SET NULL (map_id)`, written by slice 8 only. Nothing writes it yet.
#
# Per campaign with forced row-level security. No existing row changes, so no row-level security
# needs opening. Not carried in a backup yet: the pictures it points to are not either
# (`OWED_TO_BACKUP`, #3274), and the two go in together.
SQL = (
    r"""
    ALTER TABLE campaign_pictures
        ADD CONSTRAINT campaign_pictures_scope UNIQUE (id, guild_id, campaign_id);

    CREATE TABLE campaign_maps (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        picture_id   BIGINT NOT NULL,
        title        TEXT NOT NULL DEFAULT '' CHECK (char_length(title) <= 120),
        covers       TEXT,
        kind         TEXT NOT NULL CHECK (
            kind IN ('world', 'region', 'settlement', 'building', 'battle', 'other')),
        width        INTEGER CHECK (width > 0),
        height       INTEGER CHECK (height > 0),
        scale_value  DOUBLE PRECISION CHECK (scale_value > 0),
        scale_unit   TEXT CHECK (scale_unit IN ('feet', 'miles')),
        scale_from   TEXT CHECK (scale_from IN ('read', 'dm', 'import')),
        grid_kind    TEXT NOT NULL DEFAULT 'none' CHECK (grid_kind IN ('none', 'square', 'hex')),
        grid_cell_px DOUBLE PRECISION CHECK (grid_cell_px > 0),
        grid_cell_distance DOUBLE PRECISION CHECK (grid_cell_distance > 0),
        north_angle  DOUBLE PRECISION NOT NULL DEFAULT 0
            CHECK (north_angle >= 0 AND north_angle < 360),
        north_from   TEXT NOT NULL DEFAULT 'assumed'
            CHECK (north_from IN ('read', 'dm', 'assumed')),
        audience     TEXT NOT NULL CHECK (audience IN ('dm', 'table')),
        came_from    TEXT NOT NULL
            CHECK (came_from IN ('discord', 'website', 'document', 'import')),
        import_tool  TEXT NOT NULL DEFAULT '' CHECK (char_length(import_tool) <= 40),
        right_by     BIGINT NOT NULL CHECK (right_by > 0),
        right_at     BIGINT NOT NULL CHECK (right_at > 0),
        use_ok       BOOLEAN,
        use_by       BIGINT CHECK (use_by > 0),
        use_at       BIGINT CHECK (use_at > 0),
        as_of        BIGINT,
        replaces     TEXT,
        created_at   BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, picture_id),
        FOREIGN KEY (picture_id, guild_id, campaign_id)
            REFERENCES campaign_pictures (id, guild_id, campaign_id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, covers)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE SET NULL (covers) ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, replaces)
            REFERENCES campaign_maps (guild_id, campaign_id, id)
            ON DELETE SET NULL (replaces) ON UPDATE CASCADE,
        -- the DM's yes, no or not-yet: all three fields agree
        CONSTRAINT campaign_maps_use CHECK (
            (use_ok IS NULL) = (use_by IS NULL) AND (use_ok IS NULL) = (use_at IS NULL)),
        -- a scale is all of it or none; a grid names its cell or is none
        CONSTRAINT campaign_maps_scale CHECK (
            (scale_value IS NULL) = (scale_unit IS NULL)
            AND (scale_value IS NULL) = (scale_from IS NULL)),
        CONSTRAINT campaign_maps_grid CHECK (
            (grid_kind = 'none') = (grid_cell_px IS NULL AND grid_cell_distance IS NULL)),
        -- an import names its tool; nothing else does
        CONSTRAINT campaign_maps_import CHECK ((came_from = 'import') = (import_tool <> '')),
        -- only a map posted in the table's chat starts shown to the table (section 8.1)
        CONSTRAINT campaign_maps_table CHECK (audience = 'dm' OR came_from = 'discord')
    );
    CREATE INDEX campaign_maps_campaign ON campaign_maps (guild_id, campaign_id, created_at);
    CREATE INDEX campaign_maps_by_right ON campaign_maps (guild_id, right_by);
    """
    + _isolate("campaign_maps")
    + r"""

    ALTER TABLE memory_map_facts ADD COLUMN map_id TEXT;
    ALTER TABLE memory_map_facts
        ADD FOREIGN KEY (guild_id, campaign_id, map_id)
            REFERENCES campaign_maps (guild_id, campaign_id, id)
            ON DELETE SET NULL (map_id) ON UPDATE CASCADE;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("campaign_maps",)
