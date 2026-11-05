"""0350_rank_inputs_door

The counts the story summary's importance rank is worked out from (#3236).
"""

from __future__ import annotations

# A node's summary names its neighbours most important first (#3236), by the importance rank
# (`memory/importance_rank.py`). The website cannot call the bot's store, so this door hands the
# API what that counting needs and the API ranks in Python (`ranks_from`), as GraphEngineer's
# design says. It returns ids, kinds, roles, statuses, the plain connections (confirmed, not ended,
# not a player's `plays`, not made by the party), each entry's newest assessment and the sessions
# it was heard in: counts and dates only, never a name, a detail or a word of the story, and the
# API uses them and sends none to a browser. Members of the server only, as the graph doors
# (`dmbot_web_graph_source`): anyone else gets NULL. The caps are the graph door's (5,000 entries,
# 20,000 connections); past them `limited` is true and the rank is only as good as what fits.
# Nothing is written.
RANK_INPUTS_DOOR = r"""
    CREATE FUNCTION dmbot_web_rank_inputs(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'limited', (SELECT count(*) > 5000 FROM memory_entities x
                        WHERE x.guild_id = g AND x.campaign_id = c AND x.type <> 'person'
                          AND x.status IN ('proposed', 'confirmed', 'merged'))
                    OR (SELECT count(*) > 20000 FROM memory_relations x
                        WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'),
            'entities', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'type', e.type, 'role', e.role,
                           'status', e.status, 'mergedInto', e.merged_into) ORDER BY e.id)
                FROM (SELECT * FROM memory_entities x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.type <> 'person'
                        AND x.status IN ('proposed', 'confirmed', 'merged')
                      ORDER BY x.id LIMIT 5000) e), '[]'::jsonb),
            'relations', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(r.subject_id, r.object_id, r.predicate)
                                 ORDER BY r.id)
                FROM (SELECT * FROM memory_relations x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'
                        AND x.to_session_at IS NULL AND x.predicate <> 'plays'
                        AND x.source IS DISTINCT FROM 'party'
                      ORDER BY x.id LIMIT 20000) r), '[]'::jsonb),
            'assessed', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(a.entity_id, a.seq, a.rank) ORDER BY a.entity_id)
                FROM (SELECT DISTINCT ON (m.entity_id) m.entity_id, m.seq, m.rank
                      FROM memory_importance m
                      WHERE m.guild_id = g AND m.campaign_id = c
                      ORDER BY m.entity_id, m.seq DESC
                      LIMIT 5000) a), '[]'::jsonb),
            'heard', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(h.entity_id, h.session_started_at)
                                 ORDER BY h.entity_id, h.session_started_at)
                FROM (SELECT DISTINCT x.entity_id, x.session_started_at FROM memory_heard x
                      WHERE x.guild_id = g AND x.campaign_id = c
                      ORDER BY x.entity_id, x.session_started_at LIMIT 50000) h), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rank_inputs(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rank_inputs(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = RANK_INPUTS_DOOR

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_rank_inputs", "dmbot_web_rank_inputs(BIGINT, TEXT)"),
)
