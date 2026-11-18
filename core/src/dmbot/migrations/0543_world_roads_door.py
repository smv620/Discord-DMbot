"""0543_world_roads_door

The website lists, in World notes, a road's length a kept map disagrees with, and ✅ Keep takes it
(#4677, the web half of MapBot slice 4c-1).
"""

from __future__ import annotations

# `dmbot_web_world_roads(g, c)`: NULL unless the caller is a DM of the campaign (0404's check).
# Else `{roads, maps, pins}`, the rows `maps.store.roads_off(read_only=True)` and
# `maps_and_pins(dm=True)` read, and nothing else, so the web module runs the same pure check
# (`maps.disagree.check_distance`) and words the line the way Discord does:
# - `roads`: a kept, current `route_to` (confirmed, `to_session_at` not set) whose length is in
#   miles, was read (`length_from = 'read'`: a length the DM kept gets no note), between two
#   unmerged kept or proposed places, with their names;
# - `maps`: the maps DMbot may use (`use_ok` is true: `maps.store.may_use_map`) and no other map
#   replaces, with scale, size and north;
# - `pins`: pins of just those places on those maps, rejected ones never.
# The DM's maps only (the notes are the DM's alone). Worked out when read, never stored. The queue
# door's `world-keep` shape takes a `road:` line id besides the place and language ones: Keep makes
# the length the DM's own through the bot's own `world_notes.keep`, which has done so since 4c-1;
# there is no 🚫 for a road (`world-not` is unchanged). The door's shape check is read back from
# the database as it is and its anchor must be found once. No row changes.
_OLD = (
    "act = 'world-keep' AND clean !~ '^(place|language):[0-9a-f]{32}"
    "( (place|language):[0-9a-f]{32}){0,24}$'"
)
_NEW = (
    "act = 'world-keep' AND clean !~ '^(place|language|road):[0-9a-f]{32}"
    "( (place|language|road):[0-9a-f]{32}){0,24}$'"
)

SQL = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD
    + r"""$q$, $q$"""
    + _NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;

    CREATE FUNCTION dmbot_web_world_roads(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        roads JSONB;
        places TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'id', r.id, 'a', r.subject_id, 'b', r.object_id, 'aName', a.name,
                   'bName', b.name, 'stated', r.length_words, 'amount', r.length_amount,
                   'fromSession', r.from_session_at, 'createdAt', r.created_at)
                   ORDER BY r.created_at, r.id), '[]'::jsonb)
          INTO roads
          FROM memory_relations r
          JOIN memory_entities a ON a.guild_id = r.guild_id AND a.campaign_id = r.campaign_id
                                AND a.id = r.subject_id AND a.status IN ('confirmed', 'proposed')
                                AND a.merged_into IS NULL
          JOIN memory_entities b ON b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id
                                AND b.id = r.object_id AND b.status IN ('confirmed', 'proposed')
                                AND b.merged_into IS NULL
          WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'route_to'
            AND r.status = 'confirmed' AND r.to_session_at IS NULL
            AND r.length_unit = 'miles' AND r.length_from = 'read';
        SELECT COALESCE(array_agg(DISTINCT p), '{}') INTO places
          FROM jsonb_array_elements(roads) j,
               LATERAL unnest(ARRAY[j ->> 'a', j ->> 'b']) AS p;
        RETURN jsonb_build_object(
            'roads', roads,
            'maps', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', m.id, 'title', m.title, 'kind', m.kind, 'width', m.width,
                           'height', m.height, 'scale', m.scale_value,
                           'unit', COALESCE(m.scale_unit, ''), 'north', m.north_angle,
                           'northKnown', m.north_from <> 'assumed',
                           'createdAt', m.created_at) ORDER BY m.id)
                FROM campaign_maps m
                WHERE m.guild_id = g AND m.campaign_id = c AND m.use_ok IS TRUE
                  AND NOT EXISTS (SELECT 1 FROM campaign_maps o
                                  WHERE o.guild_id = g AND o.campaign_id = c
                                    AND o.replaces = m.id)), '[]'::jsonb),
            'pins', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'map', f.map_id, 'place', f.place_id,
                           'x', f.points[1], 'y', f.points[2]) ORDER BY f.map_id, f.place_id)
                FROM map_features f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.shape = 'pin'
                  AND f.status <> 'rejected' AND f.place_id = ANY (places)), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_world_roads(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_world_roads(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_world_roads", "dmbot_web_world_roads(BIGINT, TEXT)"),
)
