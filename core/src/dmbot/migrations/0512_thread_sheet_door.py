"""0512_thread_sheet_door

A quest's sheet on the Story Graph (#4540, the owner's request; #4476, quests slice 2, web half):
the thread card's door also gives the thread's own description, its current state, the people in
it by role and how many parts wait for the DM.
"""

from __future__ import annotations

# `dmbot_web_thread_card(g, c, t)` is made again with the same arguments and the same DM-only
# check (0511); what it gives is a superset. Added: `thread.type` and `thread.description` (the
# words DMbot read or the DM wrote, `memory_entities.description`, shown whole: no new AI call),
# `thread.state` (its current `memory_states` row, as `threads.STATE_WORDS` words it), `people`
# (the `in_thread` people, a merged one followed to the entry it became, each with the part word
# on the link), and `waiting` (the parts DMbot read that the DM has not kept: a count, never
# their words). The people a part names (`who`, `about`) follow a merge too (the reviewer's note
# on #4531). The grants of 0511 stay with the function. No row changes.
SQL = r"""
    CREATE OR REPLACE FUNCTION dmbot_web_thread_card(g BIGINT, c TEXT, t TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        name_of TEXT;
        type_of TEXT;
        desc_of TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT e.name, e.type, e.description INTO name_of, type_of, desc_of FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = t
              AND e.status = 'confirmed' AND e.merged_into IS NULL;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'thread', jsonb_build_object(
                'id', t, 'name', name_of, 'type', type_of, 'description', COALESCE(desc_of, ''),
                'state', (SELECT s.condition FROM memory_states s
                          WHERE s.guild_id = g AND s.campaign_id = c AND s.entity_id = t
                            AND s.closed_at IS NULL LIMIT 1)),
            'waiting', (SELECT count(*) FROM memory_thread_parts p
                        WHERE p.guild_id = g AND p.campaign_id = c AND p.thread_id = t
                          AND p.status = 'read' AND p.to_session_at IS NULL),
            'people', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', x.id, 'name', x.name, 'role', r.detail)
                                 ORDER BY lower(x.name), x.id)
                FROM memory_relations r
                JOIN LATERAL (
                    SELECT e.id, e.name FROM memory_entities e
                    WHERE e.guild_id = r.guild_id AND e.campaign_id = r.campaign_id
                      AND e.status = 'confirmed' AND e.merged_into IS NULL
                      AND e.id = COALESCE((SELECT m.merged_into FROM memory_entities m
                                           WHERE m.guild_id = r.guild_id
                                             AND m.campaign_id = r.campaign_id
                                             AND m.id = r.subject_id
                                             AND m.merged_into IS NOT NULL), r.subject_id)
                ) x ON true
                WHERE r.guild_id = g AND r.campaign_id = c AND r.object_id = t
                  AND r.predicate = 'in_thread' AND r.status = 'confirmed'
                  AND r.to_session_at IS NULL), '[]'::jsonb),
            'parts', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', p.id, 'kind', p.kind, 'words', p.words, 'state', p.state,
                           'underId', p.under_id,
                           'who', CASE WHEN w.id IS NULL THEN NULL
                                       ELSE jsonb_build_object('id', w.id, 'name', w.name) END,
                           'about', CASE WHEN a.id IS NULL THEN NULL
                                         ELSE jsonb_build_object('id', a.id, 'name', a.name) END,
                           'amount', p.amount, 'unit', p.unit, 'due', p.due, 'terms', p.terms,
                           'limitKind', p.limit_kind, 'dueMinute', p.due_minute,
                           'dueFrom', p.due_from, 'source', p.source,
                           'tableKnows', p.table_knows)
                           ORDER BY p.kind, COALESCE(p.position, 0), p.created_at, p.id)
                FROM memory_thread_parts p
                LEFT JOIN memory_entities w
                  ON w.guild_id = p.guild_id AND w.campaign_id = p.campaign_id
                 AND w.status = 'confirmed' AND w.merged_into IS NULL
                 AND w.id = COALESCE((SELECT m.merged_into FROM memory_entities m
                                            WHERE m.guild_id = p.guild_id
                                              AND m.campaign_id = p.campaign_id
                                              AND m.id = p.who_id AND m.merged_into IS NOT NULL),
                                           p.who_id)
                LEFT JOIN memory_entities a
                  ON a.guild_id = p.guild_id AND a.campaign_id = p.campaign_id
                 AND a.status = 'confirmed' AND a.merged_into IS NULL
                 AND a.id = COALESCE((SELECT m.merged_into FROM memory_entities m
                                            WHERE m.guild_id = p.guild_id
                                              AND m.campaign_id = p.campaign_id
                                              AND m.id = p.about_id AND m.merged_into IS NOT NULL),
                                           p.about_id)
                WHERE p.guild_id = g AND p.campaign_id = c AND p.thread_id = t
                  AND p.status <> 'rejected' AND p.to_session_at IS NULL), '[]'::jsonb),
            'bigger', (
                SELECT jsonb_build_object('id', b.id, 'name', b.name)
                FROM memory_relations r
                JOIN memory_entities b
                  ON b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id
                 AND b.id = r.object_id AND b.status = 'confirmed'
                WHERE r.guild_id = g AND r.campaign_id = c AND r.subject_id = t
                  AND r.predicate = 'branch_of' AND r.status = 'confirmed'
                  AND r.to_session_at IS NULL LIMIT 1),
            'branches', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', b.id, 'name', b.name)
                                 ORDER BY lower(b.name), b.id)
                FROM memory_relations r
                JOIN memory_entities b
                  ON b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id
                 AND b.id = r.subject_id AND b.status = 'confirmed'
                WHERE r.guild_id = g AND r.campaign_id = c AND r.object_id = t
                  AND r.predicate = 'branch_of' AND r.status = 'confirmed'
                  AND r.to_session_at IS NULL), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_thread_card(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
