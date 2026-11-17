"""0531_map_pins_web

The website reads a map's pins, scale, grid and north (#4672, the web half of MapBot slice 4a): a
DM-only read door.
"""

from __future__ import annotations

# `dmbot_web_map_features(g, c, m)`: NULL for anyone who is not a DM of the campaign (or a server
# the caller is not in), as 0519's door; else `{status: 'not_found'}` for a map that is not in the
# campaign, or `{status: 'ok', audience, scale, grid, north, features}`:
# - `scale`: `{value, unit, from}` or null when the map has none (DMbot never guesses one);
# - `grid`: `{kind, cellPx, cellDistance, from}` (`kind` 'none' with no grid);
# - `north`: `{angle, from}` (`from` 'assumed' means up);
# - `features`: the marks not turned down (`status` 'read' or 'confirmed'), oldest first, each
#   `{id, shape, points (fractions 0 to 1, across then down, in pairs), label, status, source,
#   targetKind ('place' | 'road' | 'area' | null), target}`: `target` is the place's name, or a
#   road's two ends by name, alphabetically ("A to B": a road runs both ways; only while the road
#   is a kept connection: a road still waiting for the DM reads as no target), or an area fact's
#   words; null for a label DMbot read but could not
#   match to a kept name ("Not matched to a name yet"). No entry id leaves the door.
# Nothing is shown to the table by this door: it answers only a DM of the campaign, whatever the
# map's audience, and a table-visible map's pins are not made visible by it (4b's answers are,
# behind `DMBOT_MAP_MEASURE`). A place entry has no coordinate of its own: a point is only ever
# on its map.
# Read only; no row is changed.
SQL = r"""
    CREATE FUNCTION dmbot_web_map_features(g BIGINT, c TEXT, m TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        k RECORD;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO k FROM campaign_maps x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.id = m;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok', 'audience', k.audience,
            'scale', CASE WHEN k.scale_value IS NULL THEN NULL
                          ELSE jsonb_build_object('value', k.scale_value, 'unit', k.scale_unit,
                                                  'from', k.scale_from) END,
            'grid', jsonb_build_object('kind', k.grid_kind, 'cellPx', k.grid_cell_px,
                                       'cellDistance', k.grid_cell_distance,
                                       'from', k.grid_from),
            'north', jsonb_build_object('angle', k.north_angle, 'from', k.north_from),
            'features', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', f.id, 'shape', f.shape, 'points', to_jsonb(f.points),
                    'label', f.label, 'status', f.status, 'source', f.source,
                    'targetKind', CASE WHEN f.place_id IS NOT NULL THEN 'place'
                                       WHEN f.relation_id IS NOT NULL AND EXISTS (
                                           SELECT 1 FROM memory_relations r
                                           WHERE r.guild_id = g AND r.campaign_id = c
                                             AND r.id = f.relation_id
                                             AND r.status = 'confirmed') THEN 'road'
                                       WHEN f.fact_id IS NOT NULL THEN 'area' END,
                    'target', CASE
                        WHEN f.place_id IS NOT NULL THEN (
                            SELECT e.name FROM memory_entities e
                            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = f.place_id)
                        WHEN f.relation_id IS NOT NULL THEN (
                            SELECT LEAST(s.name, t.name) || ' to ' || GREATEST(s.name, t.name)
                            FROM memory_relations r
                            JOIN memory_entities s ON s.guild_id = r.guild_id
                                AND s.campaign_id = r.campaign_id AND s.id = r.subject_id
                            JOIN memory_entities t ON t.guild_id = r.guild_id
                                AND t.campaign_id = r.campaign_id AND t.id = r.object_id
                            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = f.relation_id
                              AND r.status = 'confirmed')
                        WHEN f.fact_id IS NOT NULL THEN (
                            SELECT NULLIF(mf.words, '') FROM memory_map_facts mf
                            WHERE mf.guild_id = g AND mf.campaign_id = c AND mf.id = f.fact_id)
                        END)
                    ORDER BY f.created_at, f.id)
                FROM map_features f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.map_id = m
                  AND f.status <> 'rejected'), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_map_features(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_map_features(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_map_features", "dmbot_web_map_features(BIGINT, TEXT, TEXT)"),
)
