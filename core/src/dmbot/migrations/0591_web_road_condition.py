"""0591_web_road_condition

The website shows a road's or route place's condition (open, hard going, closed, gone, or the DM's
own words) and lets the DM set it, take it off, keep DMbot's read and show or hide it (#4451, the
web half of #4258 slice 4a), level with Discord's "🚧 Condition", "✅ Condition is right" and
"🙈 / 👁️".
"""

from __future__ import annotations

# `dmbot_web_road_conditions(g, c, ids)`: a member of the campaign's server gets, for each id
# asked that is a known `route_to` connection (`route_state = ''`) or a route place
# (`form = 'route'`), `{id, kind: 'connection' | 'place', value, words, read, shown, since,
# before}`. `value` is null for a route with no condition yet, which the DM alone is told about so
# the page can offer 🚧 Condition; `read` says DMbot read it and the DM has not kept it, `shown`
# that the table may be told, `since` is the number of the session it began in (the transcripts'
# own numbering, null before any), and `before` is `{value, words}` of the condition it ended.
# **Players are told only of a condition the DM kept and showed** (the players' rule of
# `map_facts.for_players`, applied again here, and for the row it ended too); a connection is a
# road for a player only once confirmed. The page asks only for the ids it draws, which the
# graph's own gate already limited.
#
# Three actions go on the queue door, each filed under no name, no target and no flag; the door
# checks only the shapes and the bot checks the rest again. `road-condition`:
# `<c|p> <id> <open|hard|closed|gone|none>` or `<c|p> <id> o <the DM's words, 1 to 60>` (`c` for a
# connection, `p` for a place; `none` is "Not sure": take the condition off; `o` is "Something
# else", one letter so that the longest text stays within the door's 100). `road-condition-keep`:
# `c <id>` (✅ Condition is right). `road-condition-show`: `c <id> <1|0>` (👁️ / 🙈). The page's
# Undo is `route-undo` (0589), which the bot widens to these. The queue door's lists, shape check
# and the table's action list are read back as they are and each anchor must be found exactly
# the number of times given. No row changes.
ACTIONS = ("road-condition", "road-condition-keep", "road-condition-show")
_ID = "[0-9a-f]{32}"
_QUEUE_SWAPS = (
    # the allowed actions, the actions without a flag, and the ones filed under no name
    (
        "'world-bias', 'route-length'",
        "'world-bias', 'road-condition', 'road-condition-keep', 'road-condition-show', "
        "'route-length'",
        3,
    ),
    # the text's shapes, before the Undo's of World notes
    (
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        f"           OR (act = 'road-condition' AND clean !~ "
        f"'^[cp] {_ID} (open|hard|closed|gone|none|o .{{1,60}})$')\n"
        f"           OR (act = 'road-condition-keep' AND clean !~ '^c {_ID}$')\n"
        f"           OR (act = 'road-condition-show' AND clean !~ '^c {_ID} [01]$')\n"
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        1,
    ),
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and three more added at the end.
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_action_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, '])', ''))) <> 2 THEN
            RAISE EXCEPTION 'the action list did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(d, '])', ', """
    + _ADDED
    + r"""])');
    END
    $do$;

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
              AND p.proname = 'dmbot_web_name_action_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;

    CREATE FUNCTION dmbot_web_road_conditions(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        SELECT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) INTO is_dm;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', t.id, 'kind', t.kind, 'value', cur.value, 'words', cur.words,
                       'read', cur.status = 'read', 'shown', cur.table_knows,
                       'since', (SELECT s.n FROM (
                                     SELECT x.started_at,
                                            row_number() OVER (ORDER BY x.started_at, x.id) AS n
                                     FROM transcript_sessions x
                                     WHERE x.guild_id = g AND x.campaign_id = c
                                       AND (x.line_count > 0
                                            OR x.transcripts_deleted_at IS NOT NULL)) s
                                 WHERE s.started_at = cur.from_session_at),
                       'before', CASE WHEN b.id IS NULL THEN NULL
                                 ELSE jsonb_build_object('value', b.value, 'words', b.words) END)
                       ORDER BY t.id)
            FROM (
                SELECT r.id, 'connection' AS kind
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (ids)
                  AND r.predicate = 'route_to' AND r.route_state = ''
                  AND r.to_session_at IS NULL
                  AND (r.status = 'confirmed' OR (is_dm AND r.status <> 'rejected'))
                UNION ALL
                SELECT e.id, 'place'
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids)
                  AND e.form = 'route' AND e.status = 'confirmed' AND e.merged_into IS NULL
                LIMIT 20000) t
            LEFT JOIN LATERAL (
                SELECT f.* FROM memory_map_facts f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.layer = 'condition'
                  AND f.status <> 'rejected' AND f.to_session_at IS NULL
                  AND (f.entity_id = t.id OR f.relation_id = t.id)
                  AND (is_dm OR (f.status = 'confirmed' AND f.table_knows))
                ORDER BY f.created_at DESC, f.id DESC LIMIT 1) cur ON TRUE
            LEFT JOIN LATERAL (
                SELECT f.* FROM memory_map_facts f
                WHERE cur.id IS NOT NULL
                  AND f.guild_id = g AND f.campaign_id = c AND f.layer = 'condition'
                  AND f.status <> 'rejected' AND f.to_session_at IS NOT NULL
                  AND (f.entity_id = t.id OR f.relation_id = t.id)
                  AND (is_dm OR (f.status = 'confirmed' AND f.table_knows))
                  AND (f.value, f.words) IS DISTINCT FROM (cur.value, cur.words)
                ORDER BY f.to_session_at DESC, f.created_at DESC, f.id DESC LIMIT 1) b ON TRUE
            WHERE is_dm OR cur.id IS NOT NULL), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_road_conditions(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_road_conditions(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_road_conditions", "dmbot_web_road_conditions(BIGINT, TEXT, TEXT[])"),
)
