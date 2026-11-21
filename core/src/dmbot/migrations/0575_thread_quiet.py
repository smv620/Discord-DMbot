"""0575_thread_quiet

The stalled count of a story thread in one place (#3696; Supervisor's ruling (a), 2026-11-21): how
many known sessions started since the thread was last touched, for the bot's after-session line
and the website's Threads panel alike.
"""

from __future__ import annotations

# "Stalled is worked out, never stored": the rule (`threads.is_stalled(state, quiet, after)`) stays
# in Python, and only its input `quiet` is counted here, so the bot's `store._threads` and the
# website's read door can't disagree. The function reads exactly what `store._threads` read before
# (its tests compare the two on the same campaigns):
# - touched: the newest of the entry's `created_at`; each state row's `from_session_at`, else its
#   `created_at`; each open, confirmed `in_thread`, `advances`, `resolves`, `promised_by` or
#   `promised_to` fact to it from a confirmed entry (its `from_session_at` and `created_at`); and
#   each session the thread's name was heard in (`memory_heard`);
# - quiet: how many of the campaign's known sessions (the distinct `memory_heard` session starts,
#   so a session where nobody was heard does not count) started after `touched`.
# SECURITY INVOKER: the caller's row-level security applies (the bot's scoped connection, or the
# website's door, which checks the DM first). The search path is pinned like every function of
# ours, so a caller's own path can't redirect a table name. No table, no row changes.
SQL = r"""
    CREATE FUNCTION dmbot_thread_quiet(p_guild_id bigint, p_campaign_id text, p_thread_ids text[])
        RETURNS TABLE (entity_id text, touched_at bigint, quiet integer)
        LANGUAGE sql STABLE
        AS $fn$
        WITH known AS (
            SELECT DISTINCT h.session_started_at AS at FROM memory_heard h
                WHERE h.guild_id = p_guild_id AND h.campaign_id = p_campaign_id
        ), touched AS (
            SELECT e.id, GREATEST(
                e.created_at,
                (SELECT max(COALESCE(s.from_session_at, s.created_at)) FROM memory_states s
                    WHERE s.guild_id = e.guild_id AND s.campaign_id = e.campaign_id
                    AND s.entity_id = e.id),
                (SELECT max(GREATEST(r.created_at, COALESCE(r.from_session_at, r.created_at)))
                    FROM memory_relations r JOIN memory_entities o
                    ON o.guild_id = r.guild_id AND o.campaign_id = r.campaign_id
                    AND o.id = r.subject_id AND o.status = 'confirmed'
                    WHERE r.guild_id = e.guild_id AND r.campaign_id = e.campaign_id
                    AND r.object_id = e.id AND r.status = 'confirmed'
                    AND r.to_session_at IS NULL
                    AND r.predicate IN ('in_thread', 'advances', 'resolves', 'promised_by',
                                        'promised_to')),
                (SELECT max(h.session_started_at) FROM memory_heard h
                    WHERE h.guild_id = e.guild_id AND h.campaign_id = e.campaign_id
                    AND h.entity_id = e.id)
            ) AS at
            FROM memory_entities e
            WHERE e.guild_id = p_guild_id AND e.campaign_id = p_campaign_id
            AND e.id = ANY(p_thread_ids)
        )
        SELECT t.id, t.at, (SELECT count(*)::integer FROM known k WHERE k.at > t.at)
            FROM touched t
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_thread_quiet(bigint, text, text[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
