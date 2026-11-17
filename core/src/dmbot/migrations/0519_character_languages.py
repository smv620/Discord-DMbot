"""0519_character_languages

The website shows and changes a character's languages (#4271, #4229 slice 1b, web half): a DM-only
read door, and four more actions on the queue door, level with Discord's 🗣️ Languages screen.
"""

from __future__ import annotations

# `dmbot_web_languages(g, c, e)`: NULL for anyone who is not a DM of the campaign (or a server the
# caller is not in), as 0505's door; else `{status: 'not_found'}` for an entry that is not in the
# campaign, or `{status: 'ok', hasSheet, languages}`: the languages as Discord's line says them
# (`MemoryStore.languages_known`): the sheet's when the character has a sheet (each `{name, source:
# 'sheet'}`, nothing to change here: the sheet is the source of truth), else the story's, each
# `{id, name, source: 'dm' | 'read'}` in the order given, with the DM's own no (`given_by = 'not'`,
# #4362) never shown. A language row's id is not an entry id; the page needs it to take one away.
# Read only, DM only, for one character at a time: never a list across characters, never players.
#
# The four actions go on the queue door as 0514's did, filed under no name with no target and no
# flag; the door checks only their shape, the bot applies each through the store call Discord's
# screen makes (`add_story_language`, `remove_story_language`, `check_story_languages`, `undo`),
# one change with one Undo, and checks the rest again (the character, a sheet, the DM):
# - `lang-add`: `<entry id> <the language's name>` (1 to 60 characters, the store's limit);
# - `lang-remove`: `<language row id>`; `lang-check`: `<entry id>` ("these are right");
# - `lang-undo`: the change number one of these made, checked by the bot against the DM's own done
#   language actions.
# The queue door's lists are read back from the database as they are and each anchor must be found
# exactly once, so a drift stops the migration. No row is changed.
ACTIONS = ("lang-add", "lang-remove", "lang-check", "lang-undo")
_ID = "[0-9a-f]{32}"

_SHAPES = (
    f"           OR (act = 'lang-add' AND clean !~ '^{_ID} [^ ].{{0,59}}$')\n"
    f"           OR (act IN ('lang-remove', 'lang-check') AND clean !~ '^{_ID}$')\n"
    f"           OR (act = 'lang-undo' AND clean !~ '^[0-9]{{1,15}}$')\n"
)
_LIST = (
    "'part-add', 'part-fix', 'part-state', 'part-show', 'part-remove', 'branch-move',"
    " 'branch-not', 'part-undo', 'part-date'"
)
_NEW_LIST = _LIST + ", " + ", ".join(f"'{a}'" for a in ACTIONS)

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (f"{_LIST})\n        THEN", f"{_NEW_LIST})\n        THEN", 1),
    # their shape, after 0515's
    (
        f"           OR (act = 'part-date' AND clean !~ '^{_ID} (-|[1-9][0-9]{{0,3}})$')\n",
        f"           OR (act = 'part-date' AND clean !~ '^{_ID} (-|[1-9][0-9]{{0,3}})$')\n"
        + _SHAPES,
        1,
    ),
    # no flag
    (
        f"{_LIST})\n               AND COALESCE(fl, false))",
        f"{_NEW_LIST})\n               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (f"{_LIST}) THEN", f"{_NEW_LIST}) THEN", 1),
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
    -- The allowed actions, read back from the table as it is and four more added at the end.
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

    CREATE FUNCTION dmbot_web_languages(g BIGINT, c TEXT, e TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        sheet BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM memory_entities x
                       WHERE x.guild_id = g AND x.campaign_id = c AND x.id = e) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        sheet := EXISTS (SELECT 1 FROM character_sheets s
                         WHERE s.guild_id = g AND s.campaign_id = c AND s.entity_id = e);
        RETURN jsonb_build_object(
            'status', 'ok', 'hasSheet', sheet,
            'languages', CASE WHEN sheet THEN COALESCE((
                    SELECT jsonb_agg(jsonb_build_object('name', lang.name, 'source', 'sheet')
                                     ORDER BY lang.n)
                    FROM character_sheets s,
                         jsonb_array_elements_text(
                             CASE WHEN jsonb_typeof(s.sheet -> 'languages') = 'array'
                                  THEN s.sheet -> 'languages' ELSE '[]'::jsonb END)
                         WITH ORDINALITY AS lang(name, n)
                    WHERE s.guild_id = g AND s.campaign_id = c AND s.entity_id = e
                      AND btrim(lang.name) <> ''), '[]'::jsonb)
                ELSE COALESCE((
                    SELECT jsonb_agg(jsonb_build_object('id', l.id, 'name', l.name,
                                                        'source', l.given_by)
                                     ORDER BY l.created_at, l.id)
                    FROM memory_story_languages l
                    WHERE l.guild_id = g AND l.campaign_id = c AND l.entity_id = e
                      AND l.given_by <> 'not'), '[]'::jsonb) END);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_languages(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_languages(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_languages", "dmbot_web_languages(BIGINT, TEXT, TEXT)"),
)
