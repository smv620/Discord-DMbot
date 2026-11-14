"""0487_maps_doors

The Maps page's doors (#4347, MapBot slice 3c): a read door that enforces the player rule itself,
and five more website actions on the queue door, level with Discord's map questions (#4371, #4373,
#4390).
"""

from __future__ import annotations

# `dmbot_web_maps(g, c)`: NULL for anyone who is not a member of the campaign's server, else the
# maps this caller may see, oldest first. A DM of the campaign sees every map, with whether it is
# shown to the table and its state (waiting, usable, declined). Anyone else sees the maps a DM
# has shown to the table (`audience = 'table'`) and said yes to (`use_ok`), and their own
# (`right_by` is their id), and only their own rows carry `shown` and `state`: a player is never
# sent a row for a map hidden from them that they did not add, nor a sign that one exists.
# "Anyone in the server" is the gallery's own rule (`dmbot_web_pictures`: a picture is private to
# the campaign, not to its game masters); the poster's name is the one the session kept (a
# website upload has none: "Someone"; and `via` is "discord" for a picture from the chat and
# "website" for any other, an import or a document included). A removed picture is not listed.
# Words, ids and the picture's open code only; no file key leaves this door.
#
# The five actions go on the queue door as `world-keep` did (0481), filed under no name, no
# target, no flag; the door checks only each text's shape and the bot rechecks everything (the
# DM, the poster, the map): `maps-use` `<map id> yes|no`, `maps-show` `<map id> shown|hidden`,
# `maps-remove` and `maps-takeback` `<map id>`, and `maps-undo` a change number one of those made
# (the bot's guard, as `world-undo`). The queue door and its lists are read back as they are
# (many migrations changed them) and each anchor must be found exactly once. No row is changed.
ACTIONS = ("maps-use", "maps-show", "maps-remove", "maps-takeback", "maps-undo")
_MAP = "[0-9a-f]{32}"

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (
        "'met-name', 'met-join', 'met-remove', 'world-keep', 'world-not',"
        " 'world-undo')\n        THEN",
        "'met-name', 'met-join', 'met-remove', 'world-keep', 'world-not', 'world-undo',"
        " 'maps-use', 'maps-show', 'maps-remove', 'maps-takeback', 'maps-undo')\n        THEN",
        1,
    ),
    # their shape: a map id and a word, a map id, or a change number
    (
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ ",
        f"           OR (act = 'maps-use' AND clean !~ '^{_MAP} (yes|no)$')\n"
        f"           OR (act = 'maps-show' AND clean !~ '^{_MAP} (shown|hidden)$')\n"
        f"           OR (act IN ('maps-remove', 'maps-takeback') AND clean !~ '^{_MAP}$')\n"
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n"
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ ",
        1,
    ),
    # no flag (the list that ends in a flag check)
    (
        "'met-name', 'met-join', 'met-remove', 'world-keep', 'world-not', 'world-undo')\n"
        "               AND COALESCE(fl, false))",
        "'met-name', 'met-join', 'met-remove', 'world-keep', 'world-not', 'world-undo',"
        " 'maps-use', 'maps-show', 'maps-remove', 'maps-takeback', 'maps-undo')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (
        "OR act IN ('confirm-kinds', 'confirm-undo', 'offer-clear', 'clock-set', 'clock-skip',"
        " 'world-keep', 'world-not', 'world-undo') THEN",
        "OR act IN ('confirm-kinds', 'confirm-undo', 'offer-clear', 'clock-set', 'clock-skip',"
        " 'world-keep', 'world-not', 'world-undo', 'maps-use', 'maps-show', 'maps-remove',"
        " 'maps-takeback', 'maps-undo') THEN",
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
    -- The allowed actions, read back from the table as it is and five more added at the end.
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

    CREATE FUNCTION dmbot_web_maps(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        RETURN jsonb_build_object(
            'status', 'ok', 'isDm', is_dm,
            'maps', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', m.id, 'title', m.title, 'code', p.code,
                        'poster', COALESCE(NULLIF(t.speaker_names ->> p.poster_id::TEXT, ''),
                                           'Someone'),
                        'via', CASE WHEN m.came_from = 'discord' THEN 'discord' ELSE 'website' END,
                        'mine', m.right_by = dmbot_current_user())
                    || CASE WHEN is_dm OR m.right_by = dmbot_current_user() THEN
                        jsonb_build_object(
                            'shown', m.audience = 'table',
                            'state', CASE WHEN m.use_ok IS TRUE THEN 'usable'
                                          WHEN m.use_ok IS FALSE THEN 'declined'
                                          ELSE 'waiting' END)
                       ELSE '{}'::jsonb END
                    ORDER BY m.created_at, m.id)
                FROM campaign_maps m
                JOIN campaign_pictures p
                  ON p.id = m.picture_id AND p.guild_id = m.guild_id
                 AND p.campaign_id = m.campaign_id AND p.deleted_at IS NULL
                LEFT JOIN transcript_sessions t
                  ON t.guild_id = p.guild_id AND t.campaign_id = p.campaign_id
                 AND t.id = p.session_id
                WHERE m.guild_id = g AND m.campaign_id = c
                  AND (is_dm OR m.right_by = dmbot_current_user()
                       OR (m.audience = 'table' AND m.use_ok IS TRUE))), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_maps(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_maps(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_maps", "dmbot_web_maps(BIGINT, TEXT)"),
)
