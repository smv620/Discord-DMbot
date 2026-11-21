"""0576_web_plot_threads

The website's Plot threads panel (#3696, slice 1; Supervisor's ruling): the campaign's story threads
with their states, who is involved, the events that moved them and the stalled count, for the DM.
"""

from __future__ import annotations

# `dmbot_web_plot_threads(g, c)` reads what `MemoryStore._threads` reads for the bot's own card:
# every confirmed entry of a thread kind (thread, quest, mystery, hook, goal, promise: the first
# 100 by name, with the total), each one's state rows (the history), the open confirmed facts that
# touch them (involved, advances, resolves, promised by and to, and the led-to links) with the
# names of the confirmed entries at their other end, the stalled count from `dmbot_thread_quiet`
# (0575: the one function the bot's after-session line calls too, so the two cannot disagree), and
# the campaign's session numbers (a start time to its number, as the transcript pages say), so the
# history reads "Open (Session 2)". The rule itself, `threads.is_stalled`, stays in Python and the
# web module applies it: stalled is worked out, never stored. The door gives the count function
# only the guild and campaign it has checked the DM against, never ids a caller supplied. DM only
# (NULL for anyone else). Names, ids, states, counts and session numbers: no transcript or card
# word (a thread's free-text description goes out for a promise only: its due note). The kinds are
# the six the seed ontology gives a thread (a test pins them). No table, no row changes.
# (`dmbot_web_threads` is the upload story threads' door, #4457.)
SQL = r"""
    CREATE FUNCTION dmbot_web_plot_threads(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        kinds TEXT[] := ARRAY['thread', 'quest', 'mystery', 'hook', 'goal', 'promise'];
        ids TEXT[];
        total BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT count(*) INTO total FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.type = ANY (kinds)
              AND e.status = 'confirmed';
        SELECT COALESCE(array_agg(t.id ORDER BY lower(t.name), t.id), '{}') INTO ids
            FROM (SELECT e.id, e.name FROM memory_entities e
                  WHERE e.guild_id = g AND e.campaign_id = c AND e.type = ANY (kinds)
                    AND e.status = 'confirmed'
                  ORDER BY lower(e.name), e.id LIMIT 100) t;
        RETURN (
            WITH f AS (
                SELECT r.subject_id, r.predicate, r.object_id, r.detail, r.from_session_at,
                       r.created_at, r.id
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c
                  AND ((r.object_id = ANY (ids) AND r.predicate IN
                            ('in_thread', 'advances', 'resolves', 'promised_by', 'promised_to'))
                       OR (r.predicate = 'led_to'
                           AND (r.object_id = ANY (ids) OR r.subject_id = ANY (ids))))
                  AND r.status = 'confirmed' AND r.to_session_at IS NULL
            )
            SELECT jsonb_build_object(
                'total', total,
                'threads', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                               'id', e.id, 'name', e.name, 'type', e.type,
                               'description', CASE WHEN e.type = 'promise'
                                                   THEN e.description ELSE '' END)
                           ORDER BY lower(e.name), e.id)
                    FROM memory_entities e
                    WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids)),
                    '[]'::jsonb),
                'states', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                               'entity', s.entity_id, 'condition', s.condition,
                               'fromSession', s.from_session_at, 'createdAt', s.created_at,
                               'closedAt', s.closed_at)
                           ORDER BY s.created_at, COALESCE(s.closed_at, 9223372036854775807),
                                    s.id)
                    FROM memory_states s
                    WHERE s.guild_id = g AND s.campaign_id = c AND s.entity_id = ANY (ids)),
                    '[]'::jsonb),
                'facts', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                               'subject', f.subject_id, 'predicate', f.predicate,
                               'object', f.object_id, 'detail', f.detail,
                               'fromSession', f.from_session_at)
                           ORDER BY f.created_at, f.id) FROM f), '[]'::jsonb),
                'names', COALESCE((
                    SELECT jsonb_object_agg(o.id, o.name)
                    FROM memory_entities o
                    WHERE o.guild_id = g AND o.campaign_id = c AND o.status = 'confirmed'
                      AND o.id IN (SELECT subject_id FROM f UNION SELECT object_id FROM f)),
                    '{}'::jsonb),
                'quiet', COALESCE((
                    SELECT jsonb_object_agg(q.entity_id, jsonb_build_array(q.touched_at, q.quiet))
                    FROM dmbot_thread_quiet(g, c, ids) q), '{}'::jsonb),
                'numbers', COALESCE((
                    SELECT jsonb_object_agg(n.started_at::text, n.number)
                    FROM (SELECT started_at, number
                          FROM (SELECT started_at, line_count,
                                       row_number() OVER (ORDER BY started_at, id) AS number
                                FROM transcript_sessions
                                WHERE guild_id = g AND campaign_id = c
                                  AND (line_count > 0 OR transcripts_deleted_at IS NOT NULL)) s
                          WHERE line_count > 0) n), '{}'::jsonb)));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_plot_threads(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_plot_threads(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_plot_threads", "dmbot_web_plot_threads(BIGINT, TEXT)"),
)
