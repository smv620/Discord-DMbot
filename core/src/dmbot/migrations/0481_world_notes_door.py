"""0481_world_notes_door

World notes on the website (#4285 slice 1c): a DM-only read door, and three more website actions
on the queue door, level with Discord's 🌍 World notes screen.
"""

from __future__ import annotations

# `dmbot_web_world_notes(g, c)`: NULL for anyone who is not a DM of the campaign (or a server the
# caller is not in), as 0463's door; else the campaign's open notes, places then languages, oldest
# first, by the rule `memory/world_notes.read` keeps (copied exactly, so the page and Discord never
# differ):
# - a place's land, climate or ruler DMbot read (`memory_map_facts` still 'read', open), on an
#   entry the DM keeps (confirmed or waiting, not merged), and a ruler only when that group is kept
#   too;
# - a language DMbot read (`memory_story_languages`, given_by 'read') for a character with no
#   sheet, and never one that is a question in Questions for you, waiting or answered (any
#   `memory_trait_offers` row of kind 'language' for that character and word: Supervisor's ruling).
# The DM's own list: names and the lists' own words, never a line of lore or transcript.
#
# The actions go on the queue door like `match-confirm` (0372). `world-keep` carries the line ids
# the page showed, space-separated (`place:<id>` / `language:<id>`), at most 25: the most Discord's
# list shows at once (`ui/world_notes.MENU_MAX`), so Keep all keeps only lines the DM saw.
# `world-not` carries one line id, and `world-undo` the change number one of those made. All are
# filed under no name, with no target and no flag, and the door checks only their shape. The bot
# applies them through the store (`keep_world_notes`, `turn_down_world_note`), which checks every
# line again, so a line gone meanwhile is answered "gone". An Undo's number is checked by the bot
# (`ActionStore.world_batch_made` is the guard: only a change this DM's own World notes action
# made; a made-up number is queued and answered "gone"). The queue door, its allowed actions and
# its text limit are read back from the database as they are (many migrations have changed them)
# and each anchor must be found exactly once, so a drift stops the migration.
# No row is changed.
ACTIONS = ("world-keep", "world-not", "world-undo")
_LINE = "(place|language):[0-9a-f]{32}"

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (
        "'met-name', 'met-join', 'met-remove')\n        THEN",
        "'met-name', 'met-join', 'met-remove', 'world-keep', 'world-not',"
        " 'world-undo')\n        THEN",
        1,
    ),
    # Keep all's text is up to 25 line ids
    (
        "WHEN act IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who',"
        " 'confirm-kinds')",
        "WHEN act IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who', 'confirm-kinds',"
        " 'world-keep')",
        1,
    ),
    # their shape: line ids only
    (
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ ",
        f"           OR (act = 'world-keep' AND clean !~ '^{_LINE}( {_LINE}){{0,24}}$')\n"
        f"           OR (act = 'world-not' AND clean !~ '^{_LINE}$')\n"
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n"
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ ",
        1,
    ),
    # no flag (the list that ends in a flag check)
    (
        "'met-name', 'met-join', 'met-remove')\n               AND COALESCE(fl, false))",
        "'met-name', 'met-join', 'met-remove', 'world-keep', 'world-not', 'world-undo')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (
        "OR act IN ('confirm-kinds', 'confirm-undo', 'offer-clear', 'clock-set',"
        " 'clock-skip') THEN",
        "OR act IN ('confirm-kinds', 'confirm-undo', 'offer-clear', 'clock-set', 'clock-skip',"
        " 'world-keep', 'world-not', 'world-undo') THEN",
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

    -- The text limit, read back as it is: Keep all's ids get the long limit too. The new case goes
    -- before the players-line case, which must be there exactly once (the length check).
    DO $do$
    DECLARE
        d text;
        fresh text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        fresh := replace(d, 'WHEN (action = ''players-line''::text) THEN 120',
                         'WHEN (action = ''world-keep''::text) THEN 1700'
                         || ' WHEN (action = ''players-line''::text) THEN 120');
        IF fresh = d OR (length(fresh) - length(d))
                        <> length(' WHEN (action = ''world-keep''::text) THEN 1700') THEN
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

    CREATE FUNCTION dmbot_web_world_notes(g BIGINT, c TEXT) RETURNS JSONB
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
            'groups', jsonb_build_array(
                jsonb_build_object('kind', 'place', 'lines', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                               'id', 'place:' || f.id, 'kind', 'place', 'entityId', f.entity_id,
                               'name', e.name, 'layer', f.layer,
                               'value', CASE WHEN f.layer = 'control' THEN f.faction_id
                                             ELSE f.value END,
                               'words', f.words, 'part', f.part,
                               'how', NULLIF(f.how, ''), 'ruler', r.name,
                               'fromSession', f.from_session_at,
                               'from', CASE WHEN f.from_session_at IS NULL THEN 'lore'
                                            ELSE 'session' END)
                               ORDER BY f.created_at, f.id)
                    FROM memory_map_facts f
                    JOIN memory_entities e
                      ON e.guild_id = f.guild_id AND e.campaign_id = f.campaign_id
                     AND e.id = f.entity_id AND e.status IN ('confirmed', 'proposed')
                     AND e.merged_into IS NULL
                    LEFT JOIN memory_entities r
                      ON r.guild_id = f.guild_id AND r.campaign_id = f.campaign_id
                     AND r.id = f.faction_id AND r.status IN ('confirmed', 'proposed')
                     AND r.merged_into IS NULL
                    WHERE f.guild_id = g AND f.campaign_id = c AND f.status = 'read'
                      AND f.to_session_at IS NULL
                      AND (f.layer <> 'control' OR r.id IS NOT NULL)), '[]'::jsonb)),
                jsonb_build_object('kind', 'language', 'lines', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                               'id', 'language:' || s.id, 'kind', 'language',
                               'entityId', s.entity_id, 'name', e.name, 'value', s.name,
                               'fromSession', NULL, 'from', 'lore')
                               ORDER BY s.created_at, s.id)
                    FROM memory_story_languages s
                    JOIN memory_entities e
                      ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id
                     AND e.id = s.entity_id AND e.status IN ('confirmed', 'proposed')
                     AND e.merged_into IS NULL
                    WHERE s.guild_id = g AND s.campaign_id = c AND s.given_by = 'read'
                      AND NOT EXISTS (SELECT 1 FROM character_sheets h
                                      WHERE h.guild_id = s.guild_id
                                        AND h.campaign_id = s.campaign_id
                                        AND h.entity_id = s.entity_id)
                      AND NOT EXISTS (SELECT 1 FROM memory_trait_offers o
                                      WHERE o.guild_id = s.guild_id
                                        AND o.campaign_id = s.campaign_id
                                        AND o.entity_id = s.entity_id AND o.kind = 'language'
                                        AND o.name_key = s.name_key)), '[]'::jsonb))));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_world_notes(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_world_notes(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_world_notes", "dmbot_web_world_notes(BIGINT, TEXT)"),
)
