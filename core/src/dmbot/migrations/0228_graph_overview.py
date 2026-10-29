"""0228_graph_overview

Every name on the Story graph, not only the centre's web (#2632).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    RELATION_DIRECTIONAL,
)

# The Story graph's overview (#2632): every name the caller may see, and which pairs of names are
# connected, so the page can say "51 names", list the names with no connection yet beside the
# graph and offer every group of linked names. The graph door (above) starts at the most-connected
# name and walks only its confirmed connections, so a young campaign drew 2 names of 51. This door
# reads the same names and the same connections by the same rules: its first half is cut out of
# that door's own text (the checks, the view, `vis` and `rel_ids`), so the two can never disagree
# about who sees what. A test compares them. Read-only: no row changes, no table, nothing in a
# backup.
# (Frozen at the graph door's text as of RELATION_DIRECTIONAL: a later migration that redefines
# `dmbot_web_relationships` must change what this slices from, or the two doors would disagree.)
_overview_door = RELATION_DIRECTIONAL[
    RELATION_DIRECTIONAL.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships(") :
]

_overview_checks = _overview_door[
    _overview_door.index("IF dmbot_current_guild() IS DISTINCT FROM g") : _overview_door.index(
        "IF start_id IS NULL THEN"
    )
]

assert "vis := ARRAY(" in _overview_checks and "rel_ids := ARRAY(" in _overview_checks

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
GRAPH_OVERVIEW = (
    """
    CREATE OR REPLACE FUNCTION dmbot_web_graph_overview(
        g BIGINT, c TEXT, cap_names INT, cap_pairs INT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        sees_all BOOLEAN;
        wanted TEXT;
        screen TEXT;
        vis TEXT[];
        rel_ids TEXT[];
    BEGIN
        """
    + _overview_checks
    + """
        RETURN jsonb_build_object(
            'status', 'ok', 'canEdit', is_dm AND sees_all, 'seesAll', sees_all,
            'view', CASE WHEN sees_all THEN 'full' ELSE 'players' END,
            'visibility', screen, 'canPeek', is_dm OR screen IN ('peek', 'open'),
            'total', COALESCE(array_length(vis, 1), 0),
            'connections', COALESCE(array_length(rel_ids, 1), 0),
            -- the names, as the graph's nodes tell them; at most `cap_names` (`total` says if more)
            'names', COALESCE((
                SELECT jsonb_agg(x.j ORDER BY x.n, x.id) FROM (
                    SELECT e.id, lower(e.name) AS n, jsonb_build_object(
                        'id', e.id, 'name', e.name,
                        'revealed', NOT (e.hidden_from_players OR e.audience_limited),
                        'kind', CASE WHEN e.type = 'character' THEN
                                    CASE e.role WHEN 'player_character' THEN 'player_character'
                                                WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                                ELSE 'character' END
                                ELSE e.type END,
                        'secret', CASE WHEN sees_all THEN EXISTS (
                            SELECT 1 FROM memory_aliases a
                            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                              AND a.status <> 'rejected' AND a.secret
                              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                                = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')) END)
                        AS j
                    FROM memory_entities e
                    WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (vis)
                    ORDER BY lower(e.name), e.id LIMIT GREATEST(cap_names, 1)) x), '[]'::jsonb),
            -- which two names each visible connection joins, once for each pair, in a fixed order
            -- so a cut list is the same on every load; `pairsMore` says if pairs were cut
            'pairs', COALESCE((
                SELECT jsonb_agg(q.p ORDER BY q.a, q.b) FROM (
                    SELECT DISTINCT LEAST(r.subject_id, r.object_id) AS a,
                                    GREATEST(r.subject_id, r.object_id) AS b,
                                    jsonb_build_array(LEAST(r.subject_id, r.object_id),
                                                      GREATEST(r.subject_id, r.object_id)) AS p
                    FROM memory_relations r
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
                    ORDER BY 1, 2 LIMIT GREATEST(cap_pairs, 1)) q), '[]'::jsonb),
            'pairsMore', (
                SELECT count(*) > GREATEST(cap_pairs, 1) FROM (
                    SELECT DISTINCT LEAST(r.subject_id, r.object_id),
                                    GREATEST(r.subject_id, r.object_id)
                    FROM memory_relations r
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)) d));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_graph_overview(BIGINT, TEXT, INT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_graph_overview(BIGINT, TEXT, INT, INT) FROM PUBLIC;
    """
)

SQL = GRAPH_OVERVIEW

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Every name the caller may see and which pairs are connected, for the Story graph's count line,
    # its "Not connected yet" tray and its other groups (#2632).
    ("dmbot_web_graph_overview", "dmbot_web_graph_overview(BIGINT, TEXT, INT, INT)"),
)
