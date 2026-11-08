"""0383_graph_cleanup_door

A DM-only door to the rows the graph clean-up page works out who is not linked to the party from
(#3740).
"""

from __future__ import annotations

# The clean-up page lists the characters and groups no chain of links reaches from a player
# character or the party (owner, 2026-11-07, #3740). The rule is one pure function in core
# (`memory/isolated.py`), shared with the Discord count, so this door only hands a DM of the
# campaign the rows it reads: every live character and group (with the parts an unnamed one's
# label is built from), every link between two of them that is kept or waiting (never a guess
# connection, #2412), and the kept scene of each (#3596; the scene sweep keeps an isolated entry's
# scene for this page). Anyone else, a member who is not a DM included, gets NULL. Names, kinds,
# link words and scenes only: what a DM already sees on the map and the name card. Nothing is
# written.
GRAPH_CLEANUP = r"""
    CREATE FUNCTION dmbot_web_graph_cleanup(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        ids TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        ids := ARRAY(
            SELECT e.id FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.merged_into IS NULL
              AND e.type IN ('character', 'faction') AND e.status IN ('proposed', 'confirmed'));
        RETURN jsonb_build_object(
            'status', 'ok',
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', e.id, 'name', e.name, 'type', e.type, 'status', e.status,
                    'role', e.role, 'party', e.party, 'unnamed', e.unnamed,
                    'unnamedWhat', e.unnamed_what,
                    'unnamedAnchor', CASE WHEN e.unnamed THEN (
                        SELECT jsonb_build_object(
                            'name', x.name, 'what', x.unnamed_what, 'anchorName', y.name)
                        FROM memory_entities x
                        LEFT JOIN memory_entities y ON y.guild_id = x.guild_id
                             AND y.campaign_id = x.campaign_id AND y.id = x.unnamed_anchor
                        WHERE x.guild_id = g AND x.campaign_id = c
                          AND x.id = e.unnamed_anchor) END)
                    ORDER BY e.id)
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids)
                  -- (again: `ids` was read a statement earlier, and an entry merged or rejected
                  -- since must not come back as live)
                  AND e.merged_into IS NULL AND e.status IN ('proposed', 'confirmed')),
                '[]'::jsonb),
            'links', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', r.id, 'a', r.subject_id, 'b', r.object_id, 'predicate', r.predicate,
                    'detail', r.detail, 'directional', r.directional, 'status', r.status,
                    'secret', r.secret, 'at', r.created_at)
                    ORDER BY r.id)
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c
                  -- (kept, or waiting for the DM; never a guess connection, which only holds
                  -- what someone believes and is no connection yet, #2412)
                  AND (r.status = 'confirmed' OR (r.status = 'proposed'
                       AND NOT (r.secret AND r.source = 'extractor')))
                  AND r.subject_id = ANY (ids) AND r.object_id = ANY (ids)), '[]'::jsonb),
            'scenes', COALESCE((
                SELECT jsonb_object_agg(s.subject_id, s.scene)
                FROM memory_scenes s
                WHERE s.guild_id = g AND s.campaign_id = c AND s.subject_kind = 'entity'
                  AND s.subject_id = ANY (ids)), '{}'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_graph_cleanup(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_graph_cleanup(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = GRAPH_CLEANUP

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_graph_cleanup", "dmbot_web_graph_cleanup(BIGINT, TEXT)"),
)
