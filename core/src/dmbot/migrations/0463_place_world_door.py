"""0463_place_world_door

A place's land, climate and ruler on the website (#4287, MapBot layers slice 2, the DM's half):
a read door for a campaign's DM and four more website actions. What players see of these is a
separate piece (#4311) built on the story graph's player-view pattern; this door answers a DM of
the campaign and nobody else, so it never needs the players' rule (`gate.visible_to`, which lives
in Python and is not copied into SQL).

`dmbot_web_place_world(g, c, place)`: NULL for anyone who is not a DM of the campaign (or a server
the caller is not in), `{"status": "not_found"}` for a name that is not a confirmed place here, else
the place's open rows (not rejected, not ended) with who gave each (`source`, `status`), and the
campaign's confirmed groups (id and name, 200 at most) for the ruler menu.

The website actions go through the queue door like `place-form`: `map-terrain` and `map-climate`
carry the value, and its shade after a space, or nothing for "Not sure"; `map-control` carries the
group's id and how it holds the place, or nothing; `map-show` carries a flag (the 🙈 / 👁️ switch).
The queue door and the allowed-action list are read back from the database as they are (many
migrations have changed them) and each anchor must be found exactly once, so a drift stops the
migration (the 0456 way). No row is changed.
"""

from __future__ import annotations

TERRAIN = "arctic|coastal|desert|forest|grassland|hill|mountain|swamp|underdark|urban|waterborne"
CLIMATE = "arctic|cold|temperate|warm|hot"
ACTIONS = ("map-terrain", "map-climate", "map-control", "map-show")

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'players-line', 'offer-clear', 'place-size', 'place-setting', 'place-form',\n",
        "'players-line', 'offer-clear', 'place-size', 'place-setting', 'place-form',\n"
        "                'map-terrain', 'map-climate', 'map-control', 'map-show',\n",
        1,
    ),
    # the text of each: a value and its shade, a group and how it holds the place, or nothing
    (
        "           OR (act = 'place-form' AND clean !~ '^(water|route)?$')\n",
        "           OR (act = 'place-form' AND clean !~ '^(water|route)?$')\n"
        f"           OR (act = 'map-terrain' AND clean !~ '^(({TERRAIN})( (mostly|partly))?)?$')\n"
        f"           OR (act = 'map-climate' AND clean !~ '^(({CLIMATE})( (dry|wet))?)?$')\n"
        "           OR (act = 'map-control'\n"
        "               AND clean !~ '^([0-9a-f]{32} (rules|claims|occupies|protects))?$')\n"
        "           OR (act = 'map-show' AND (clean <> '' OR fl IS NULL))\n",
        1,
    ),
    # no flag, except the show switch
    (
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear', 'place-size',\n"
        "                      'place-form')\n"
        "               AND COALESCE(fl, false)) THEN",
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear', 'place-size',\n"
        "                      'place-form', 'map-terrain', 'map-climate', 'map-control')\n"
        "               AND COALESCE(fl, false)) THEN",
        1,
    ),
    # the ruler must be a confirmed group of this campaign
    (
        "        PERFORM pg_advisory_xact_lock(hashtextextended('name_action:'",
        "        IF act = 'map-control' AND clean <> '' AND NOT EXISTS (\n"
        "               SELECT 1 FROM memory_entities t\n"
        "               WHERE t.guild_id = g AND t.campaign_id = c\n"
        "                 AND t.id = split_part(clean, ' ', 1) AND t.type = 'faction'\n"
        "                 AND t.status = 'confirmed' AND t.merged_into IS NULL) THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_target');\n"
        "        END IF;\n"
        "        PERFORM pg_advisory_xact_lock(hashtextextended('name_action:'",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _rebuild(door: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    steps = "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + door
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


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
    """
    + _rebuild("dmbot_web_name_action_add", _QUEUE_SWAPS)
    + r"""
    CREATE FUNCTION dmbot_web_place_world(g BIGINT, c TEXT, place TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        lvl TEXT;
        frm TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT e.place_level, e.form INTO lvl, frm FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = place
              AND e.status = 'confirmed' AND e.type = 'place' AND e.merged_into IS NULL;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok', 'level', lvl, 'form', frm,
            'rows', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', f.id, 'layer', f.layer, 'value', f.value, 'words', f.words,
                           'group', f.faction_id,
                           'groupName', (SELECT x.name FROM memory_entities x
                                         WHERE x.guild_id = g AND x.campaign_id = c
                                           AND x.id = f.faction_id),
                           'how', f.how, 'part', f.part, 'status', f.status,
                           'source', f.source, 'tableKnows', f.table_knows)
                           ORDER BY f.created_at, f.id)
                FROM memory_map_facts f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.entity_id = place
                  AND f.status <> 'rejected' AND f.to_session_at IS NULL), '[]'::jsonb),
            'groups', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name)
                                 ORDER BY lower(t.name), t.id)
                FROM (SELECT x.id, x.name FROM memory_entities x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'faction'
                        AND x.status = 'confirmed' AND x.merged_into IS NULL
                      ORDER BY lower(x.name), x.id LIMIT 200) t), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_place_world(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_place_world(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_place_world", "dmbot_web_place_world(BIGINT, TEXT, TEXT)"),
)
