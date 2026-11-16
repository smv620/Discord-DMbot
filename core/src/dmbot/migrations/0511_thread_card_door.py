"""0511_thread_card_door

A kept story thread's card on the website (#4476, quests slice 2, web half): a DM-only read door for
its steps, what is in the way, rewards, help, time limits and its branches, as Discord's 🧵 card
"📋 Steps, rewards and more" reads them.
"""

from __future__ import annotations

# `dmbot_web_thread_card(g, c, t)`: NULL for anyone who is not a DM of the campaign (or a server
# the caller is not in), as 0481's and 0505's doors; else the thread `t` if it is a kept entry
# (confirmed, not merged), with:
# - `parts`: the DM's view of its standing parts, the rule `thread_parts.parts_for(the_dm())`
#   keeps (copied exactly: not rejected, not closed into history), in the order the Discord card
#   reads them (kind, the DM's order, when written). Each has its words, state, the two people it
#   names (`who`, `about`: a kept entry's id and name, else null), the part it stands under, its
#   amount and unit, when it is due, its terms, soft or hard, the game date it falls due
#   (`dueMinute`, `dueFrom`; the page does not word it yet), where it came from and whether the
#   table knows;
# - `bigger`: the thread this one is a branch of, and `branches`: the kept threads that are
#   branches of it, A to Z (`MemoryStore.thread_card`'s `branch_of` links).
# Nothing worked out is stored or worked out here ("50 owed", "1 of 2 done" come from the web API,
# through `thread_parts.owed` and `done_count`). The ids are the DM's own page's: a part's, for the
# changes that come next, and the entries'. No row changes.
SQL = r"""
    CREATE FUNCTION dmbot_web_thread_card(g BIGINT, c TEXT, t TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        name_of TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT e.name INTO name_of FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = t
              AND e.status = 'confirmed' AND e.merged_into IS NULL;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'thread', jsonb_build_object('id', t, 'name', name_of),
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
                 AND w.id = p.who_id AND w.status = 'confirmed'
                LEFT JOIN memory_entities a
                  ON a.guild_id = p.guild_id AND a.campaign_id = p.campaign_id
                 AND a.id = p.about_id AND a.status = 'confirmed'
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
    REVOKE ALL ON FUNCTION dmbot_web_thread_card(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_thread_card", "dmbot_web_thread_card(BIGINT, TEXT, TEXT)"),
)
