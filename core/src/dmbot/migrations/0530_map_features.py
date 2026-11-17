"""0530_map_features

Positions on a map (#4670, MapBot layers slice 4a; docs/design/mapbot_layers.md §4.1 and §4.2;
Supervisor's ruling on #4670).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row is one thing marked on one map (§4.1): a `pin` (one point), a `path` (a line of points)
# or an `area` (an outline). `points` are fractions of the picture, 0 to 1 across then down, in
# pairs, so they survive a resize; a place entry never has a coordinate of its own.
# - What it marks: at most one of a place entry, a `route_to` connection or a `map_facts` row (one
#   CHECK). None is a label DMbot read and hasn't matched to a kept name (Supervisor's ruling 2: a
#   waiting name is never a target). A target that goes away leaves the feature unmatched
#   (`SET NULL` on that one column), so a map never loses what was read on it.
# - `label`: the words as written on the map. `status`: read, confirmed or rejected. `source`:
#   read (the AI), dm (placed on the website, slice 5) or import (slice 11).
# - Deleting a map deletes its features (the store does it through the change log first, so an
#   Undo of the removal puts them back; the cascade is the floor).
# - One open pin for each place on each map (the unique index), so a re-read never doubles one.
# Ids, numbers and the map's own label words only; nothing a player said.
#
# `campaign_maps.pins_read_at` (Supervisor's ruling 1): when the map's pins, scale and north were
# last read. A map read closely before this slice has none, and the one-time re-read (at most 5 a
# day per server) takes those. Housekeeping like `close_read_at` (0488): not in the change log.
#
# Per campaign with forced row-level security; every key that carries `guild_id` follows a
# campaign moved to another server (`ON UPDATE CASCADE`, #2550). Not in a backup yet: with
# `campaign_maps` and the pictures (#3274), as 0472 says. No existing row changes, so no row-level
# security needs opening.
SQL = r"""
    CREATE TABLE map_features (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        map_id          TEXT NOT NULL,
        shape           TEXT NOT NULL CHECK (shape IN ('pin', 'path', 'area')),
        points          DOUBLE PRECISION[] NOT NULL CHECK (
            cardinality(points) % 2 = 0
            AND cardinality(points) BETWEEN 2 AND 2000
            AND 0 <= ALL(points) AND 1 >= ALL(points)),
        place_id        TEXT,
        relation_id     TEXT,
        fact_id         TEXT,
        label           TEXT NOT NULL DEFAULT '' CHECK (char_length(label) <= 80),
        status          TEXT NOT NULL CHECK (status IN ('read', 'confirmed', 'rejected')),
        source          TEXT NOT NULL CHECK (source IN ('read', 'dm', 'import')),
        created_at      BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit  TEXT CONSTRAINT map_features_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        CONSTRAINT map_features_one_target
            CHECK (num_nonnulls(place_id, relation_id, fact_id) <= 1),
        CONSTRAINT map_features_shape_points CHECK (
            (shape = 'pin' AND cardinality(points) = 2)
            OR (shape = 'path' AND cardinality(points) >= 4)
            OR (shape = 'area' AND cardinality(points) >= 6)),
        FOREIGN KEY (guild_id, campaign_id, map_id)
            REFERENCES campaign_maps (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, place_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE SET NULL (place_id) ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE SET NULL (relation_id) ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, fact_id)
            REFERENCES memory_map_facts (guild_id, campaign_id, id)
            ON DELETE SET NULL (fact_id) ON UPDATE CASCADE
    );
    CREATE INDEX map_features_by_map ON map_features (guild_id, campaign_id, map_id);
    CREATE INDEX map_features_by_place
        ON map_features (guild_id, campaign_id, place_id) WHERE place_id IS NOT NULL;
    CREATE UNIQUE INDEX map_features_one_pin
        ON map_features (guild_id, campaign_id, map_id, place_id)
        WHERE shape = 'pin' AND place_id IS NOT NULL AND status <> 'rejected';
    CREATE TRIGGER map_features_stamp_commit
        BEFORE INSERT OR UPDATE ON map_features
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();

    ALTER TABLE campaign_maps ADD COLUMN pins_read_at BIGINT;
    """ + _isolate("map_features")

ISOLATED_TABLES: tuple[str, ...] = ("map_features",)
