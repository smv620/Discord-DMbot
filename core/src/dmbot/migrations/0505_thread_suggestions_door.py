"""0505_thread_suggestions_door

The story threads DMbot found in an upload, on the website (#4457, quests slice 1, web half): a
DM-only read door for the waiting suggestions, and three more website actions on the queue door,
level with Discord's 📜 Story threads card.
"""

from __future__ import annotations

# `dmbot_web_threads(g, c)`: NULL for anyone who is not a DM of the campaign (or a server the caller
# is not in), as 0481's door; else the campaign's waiting suggestions, oldest first. Each is its id,
# its kind ('quest' or 'hook'), the name DMbot built, the aim, the quote the reader checked and the
# name of the upload, with `people`: each person's part word and name, and their `index`, the
# position in the suggestion's own list, which is what a keep names. A person is shown by the kept
# entry they stand for now (a merge is followed, a short chain at most); one who is gone, turned
# down or not kept yet is left out, as the keep leaves them out. Entry ids never leave the door.
# This is the second reader of the table, on purpose (`test_thread_suggestion_readers`): the DM's
# own card only, never a count, a names list or a players' view.
#
# The actions go on the queue door as `world-keep` did (0481). `thread-keep` carries the
# suggestion's id, then the positions of the people left on (`-` for none), then optionally the
# DM's own name for the thread (1 to 100 characters, as Discord's Rename). `thread-not` carries the
# suggestion's id. `thread-undo` carries `keep <change number>` or `not <suggestion id>`. All are
# filed under no name, with no target and no flag, and the door checks only their shape. The bot
# applies them through the same store calls as Discord (`keep_thread`, `ThreadSuggestionStore`),
# which check again, so a suggestion answered meanwhile is answered "gone"; an Undo's number is
# checked by the bot against the DM's own done keeps. The queue door and its lists are read back
# from the database as they are and each anchor must be found exactly once. No row is changed.
ACTIONS = ("thread-keep", "thread-not", "thread-undo")
_ID = "[0-9a-f]{32}"

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (
        "'maps-takeback', 'maps-undo', 'maps-add')\n        THEN",
        "'maps-takeback', 'maps-undo', 'maps-add', 'thread-keep', 'thread-not',"
        " 'thread-undo')\n        THEN",
        1,
    ),
    # their shape
    (
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n"
        "           OR (act = 'thread-keep' AND clean !~"
        f" '^{_ID} (-|[0-9]{{1,2}}(,[0-9]{{1,2}}){{0,24}})( [^ ].{{0,99}})?$')\n"
        f"           OR (act = 'thread-not' AND clean !~ '^{_ID}$')\n"
        f"           OR (act = 'thread-undo' AND clean !~ '^(keep [0-9]{{1,15}}|not {_ID})$')\n",
        1,
    ),
    # no flag
    (
        "'maps-takeback', 'maps-undo', 'maps-add')\n               AND COALESCE(fl, false))",
        "'maps-takeback', 'maps-undo', 'maps-add', 'thread-keep', 'thread-not', 'thread-undo')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (
        "'maps-takeback', 'maps-undo', 'maps-add') THEN",
        "'maps-takeback', 'maps-undo', 'maps-add', 'thread-keep', 'thread-not', 'thread-undo')"
        " THEN",
        1,
    ),
    # a `thread-keep` text may be as long as the longest action's
    (
        "'alias-who', 'confirm-kinds', 'world-keep', 'maps-add')",
        "'alias-who', 'confirm-kinds', 'world-keep', 'maps-add', 'thread-keep')",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)

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

    -- The text limit, read back as it is: a `thread-keep` text gets the long limit. The new case
    -- goes before the players-line case, which must be there exactly once (the length check).
    DO $do$
    DECLARE
        d text;
        fresh text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        fresh := replace(d, 'WHEN (action = ''players-line''::text) THEN 120',
                         'WHEN (action = ''thread-keep''::text) THEN 1700'
                         || ' WHEN (action = ''players-line''::text) THEN 120');
        IF fresh = d OR (length(fresh) - length(d))
                        <> length(' WHEN (action = ''thread-keep''::text) THEN 1700') THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check ' || fresh;
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

    CREATE FUNCTION dmbot_web_threads(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'suggestions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'kind', s.kind, 'name', s.name, 'aim', s.aim,
                           'quote', s.quote, 'upload', s.upload,
                           'people', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'index', p.ord - 1, 'role', p.part ->> 0,
                                          'name', who.name) ORDER BY p.ord)
                               FROM jsonb_array_elements(s.parts) WITH ORDINALITY AS p(part, ord)
                               JOIN LATERAL (
                                   WITH RECURSIVE chain(merged_into, status, name, depth) AS (
                                       SELECT x.merged_into, x.status, x.name, 0
                                       FROM memory_entities x
                                       WHERE x.guild_id = g AND x.campaign_id = c
                                         AND x.id = p.part ->> 1
                                       UNION ALL
                                       SELECT y.merged_into, y.status, y.name, ch.depth + 1
                                       FROM chain ch
                                       JOIN memory_entities y
                                         ON y.guild_id = g AND y.campaign_id = c
                                        AND y.id = ch.merged_into
                                       WHERE ch.depth < 5)
                                   SELECT chain.name FROM chain
                                   WHERE chain.merged_into IS NULL AND chain.status = 'confirmed'
                                   ORDER BY chain.depth DESC LIMIT 1
                               ) who ON true), '[]'::jsonb))
                           ORDER BY s.created_at, s.id)
                FROM thread_suggestions s
                WHERE s.guild_id = g AND s.campaign_id = c AND s.status = 'waiting'),
                '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_threads(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_threads(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_threads", "dmbot_web_threads(BIGINT, TEXT)"),
)
