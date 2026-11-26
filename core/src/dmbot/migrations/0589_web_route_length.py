"""0589_web_route_length

The website shows a route's stated length and lets the DM set it, take it off or keep DMbot's read
(#4392, the web half of #4255), level with Discord's "🛣️ Length" and "✅ Length is right".
"""

from __future__ import annotations

# `dmbot_web_route_lengths(g, c, ids)`: a member of the campaign's server gets, for each id asked
# that is a kept `route_to` connection or a kept route place (`form = 'route'`),
# `{id, kind: 'connection' | 'place', words, from}` (`words` and `from` are null for a route with no
# length yet, which the DM alone is told about so the page can offer 🛣️ Length; `from` is `read` or
# `dm`). **Players
# never see a length DMbot read** (`length_from = 'read'`): the door leaves those rows out for
# anyone who is not a DM of the campaign, so they see one only once the DM set or kept it (the
# design basis, §4). Only the words and who gave them: the amount and unit stay in the store. The
# page asks only for the ids on the page it draws.
#
# Three actions go on the queue door. `route-length`: `<c|p> <id>[ <the DM's words>]`, `c` for a
# connection and `p` for a place, the words 1 to 60 characters and none to take the length off; the
# bot runs `MemoryStore.set_route_length`. `route-length-keep`: `<c|p> <id>`, `keep_route_length`
# (✅ Length is right: it is the DM's from now on). `route-undo`: the change number of one of those
# (the bot reads it again in this DM's own done actions, as the other Undos do). Filed under no
# name, no target and no flag; the door checks only the shapes and the bot checks the rest again.
# The queue door's lists, shape check and the table's action list are read back as they are and each
# anchor must be found exactly the number of times given. No row changes.
ACTIONS = ("route-length", "route-length-keep", "route-undo")
_ID = "[0-9a-f]{32}"
_QUEUE_SWAPS = (
    # the allowed actions, the actions without a flag, and the ones filed under no name
    (
        "'world-bias', 'maps-use'",
        "'world-bias', 'route-length', 'route-length-keep', 'route-undo', 'maps-use'",
        3,
    ),
    # the text's shapes, before the Undo's of World notes
    (
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        f"           OR (act = 'route-length' AND clean !~ '^[cp] {_ID}( .{{1,60}})?$')\n"
        f"           OR (act = 'route-length-keep' AND clean !~ '^[cp] {_ID}$')\n"
        "           OR (act = 'route-undo' AND clean !~ '^[0-9]{1,15}$')\n"
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

    CREATE FUNCTION dmbot_web_route_lengths(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
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
                       'id', t.id, 'kind', t.kind, 'words', t.words, 'from', t.src)
                       ORDER BY t.id)
            FROM (
                SELECT r.id, 'connection' AS kind, r.length_words AS words, r.length_from AS src
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (ids)
                  AND r.predicate = 'route_to' AND r.status = 'confirmed'
                  AND (is_dm OR (r.length_words IS NOT NULL AND r.length_from = 'dm'))
                UNION ALL
                SELECT e.id, 'place', e.length_words, e.length_from
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids)
                  AND e.form = 'route' AND e.status = 'confirmed' AND e.merged_into IS NULL
                  AND (is_dm OR (e.length_words IS NOT NULL AND e.length_from = 'dm'))
                LIMIT 20000) t), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_route_lengths(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_route_lengths(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_route_lengths", "dmbot_web_route_lengths(BIGINT, TEXT, TEXT[])"),
)
