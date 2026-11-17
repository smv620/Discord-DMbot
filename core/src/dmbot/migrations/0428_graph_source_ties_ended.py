"""0428_graph_source_ties_ended

The Story Graph's source door says how and when each connection ended, so the page can tell an
item's history ("Claude sold it to Belleros · Belleros has it now", #4122, the web half of #4115).
"""

from __future__ import annotations

# `dmbot_web_graph_source` already sends every confirmed connection with an `ended` flag
# (`to_session_at IS NOT NULL`). This adds, beside it, when the holder came to have it
# (`from_at`), when it ended (`to_at`) and why (`end_reason`: sold, gave, traded, stolen, lost, or
# null; migration 0422, set only on an ended owns or carries). Those are the three things
# `memory/hand_over.history` orders and words a holder's step by. The numbers are session times,
# never a word of a transcript; `graph_view.build` still shows a viewer only the connections they
# know, and a player never sees the ones the gate hides. The door is read back from the database as
# it is (many migrations have changed it) and the anchor must be found exactly once, so a drift
# stops the migration instead of editing the wrong line, as 0358 and 0413 do. No row is changed.
_OLD = "'ended', r.to_session_at IS NOT NULL,"
_NEW = (
    "'ended', r.to_session_at IS NOT NULL,\n"
    "                           'from_at', r.from_session_at, 'to_at', r.to_session_at,\n"
    "                           'end_reason', r.end_reason,"
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
              AND p.proname = 'dmbot_web_graph_source';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD
    + r"""$q$, $q$"""
    + _NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;

    -- The ties behind an item's history, for the DM's own Full View, which is read from the
    -- database as a whole and not through the gate: the confirmed owns and carries ties whose
    -- object is one of `ids` (at most 500), ended ones included, with the holder's name when the
    -- holder is a live, confirmed entry. DM of the campaign only (anyone else, NULL). Ids, names
    -- the DM already sees, session times and the reason: never a word of a transcript.
    CREATE FUNCTION dmbot_web_item_ties(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', r.id, 'a', r.subject_id, 'b', r.object_id,
                       'predicate', r.predicate, 'at', r.created_at,
                       'from_at', r.from_session_at, 'to_at', r.to_session_at,
                       'end_reason', r.end_reason, 'name', h.name)
                       ORDER BY r.created_at, r.id)
            FROM memory_relations r
            LEFT JOIN memory_entities h ON h.guild_id = r.guild_id AND h.campaign_id = r.campaign_id
                                       AND h.id = r.subject_id AND h.status = 'confirmed'
                                       AND h.merged_into IS NULL
            WHERE r.guild_id = g AND r.campaign_id = c AND r.status = 'confirmed'
              AND r.predicate IN ('owns', 'carries') AND r.source NOT IN ('plan', 'story_plan')
              AND r.object_id = ANY (ids[1:500])), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_item_ties(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_item_ties(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_item_ties", "dmbot_web_item_ties(BIGINT, TEXT, TEXT[])"),
)
