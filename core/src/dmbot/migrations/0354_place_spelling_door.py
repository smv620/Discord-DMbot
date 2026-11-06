"""0354_place_spelling_door

The website's "Is this the same place?" question (#3533, the website half of #3037).
"""

from __future__ import annotations

# A new spelling of a place the campaign already has is saved as a PROPOSED other name of that place
# (#3532). This read door lists them for a game master of the campaign: the alias id, the place's id
# and main name, and the spelling. A proposed name is the DM's alone, so the door works out the DM
# check itself (anyone else, a player included, gets NULL) and never reads another campaign. A
# spelling counts when it is the same letters as another confirmed name of the same place once the
# spaces are gone, which is the bot's own rule (`spellings.place_key`: `name_key` without spaces;
# the stored `key` is already `name_key`). The answers go through the existing queue: `spelling-yes`
# (the bot confirms the other name) and `spelling-no` (the bot turns it down for good), each with
# the alias as the target and the place as the entry. Names are shown to the DM only; nothing is
# written here.
#
# The queue door is read back from the database as it is (many migrations have changed it) and each
# anchor must be found the exact number of times, so a drift stops the migration instead of widening
# the wrong rule, as 0343 does.
_SWAPS = (
    # the allowed actions
    (
        "'name', 'name-join', 'name-undo')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        "'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        1,
    ),
    # no text with either
    (
        "'name-join', 'name-undo')\n               AND clean <> '')",
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n               AND clean <> '')",
        1,
    ),
    # both need a target (the alias) ...
    (
        "'name-join', 'name-undo')\n               AND (tgt IS NULL",
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n               AND (tgt IS NULL",
        1,
    ),
    # ... and no other action takes one
    (
        "'name-join', 'name-undo')\n               AND COALESCE(tgt, '') <> '')",
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n"
        "               AND COALESCE(tgt, '') <> '')",
        1,
    ),
    # no flag
    (
        "'name', 'name-join', 'name-undo')\n               AND COALESCE(fl, false))",
        "'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # the alias must still be a proposed other name of this place (a place, not a person or a group)
    (
        "IF act IN ('link-who', 'alias-who') AND clean LIKE 'some %' AND (",
        "IF act IN ('spelling-yes', 'spelling-no') AND NOT EXISTS (\n"
        "               SELECT 1 FROM memory_aliases a\n"
        "               JOIN memory_entities p ON p.guild_id = a.guild_id\n"
        "                    AND p.campaign_id = a.campaign_id AND p.id = a.entity_id\n"
        "               WHERE a.guild_id = g AND a.campaign_id = c AND a.id = tgt\n"
        "                 AND a.entity_id = subject AND a.status = 'proposed'\n"
        "                 AND p.type = 'place') THEN\n"
        "            RETURN jsonb_build_object('status', 'not_found');\n"
        "        END IF;\n"
        "        IF act IN ('link-who', 'alias-who') AND clean LIKE 'some %' AND (",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _SWAPS
)

READ_DOOR = r"""
    CREATE FUNCTION dmbot_web_place_spellings(g BIGINT, c TEXT) RETURNS JSONB
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
                       'id', a.id, 'placeId', e.id, 'place', e.name, 'spelling', a.text)
                   ORDER BY a.created_at, a.id)
            FROM (SELECT * FROM memory_aliases x
                  WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'proposed'
                  ORDER BY x.created_at, x.id LIMIT 200) a
            JOIN memory_entities e ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
            WHERE e.type = 'place' AND e.status = 'confirmed' AND e.merged_into IS NULL
              AND EXISTS (SELECT 1 FROM memory_aliases m
                          WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = e.id
                            AND m.id <> a.id AND m.status = 'confirmed'
                            AND replace(m.key, ' ', '') = replace(a.key, ' ', ''))
        ), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_place_spellings(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_place_spellings(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = (
    r"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other', 'secret',
         'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words', 'link-secret',
         'link-remove', 'link-pin', 'link-who', 'alias-who', 'confirm-kinds', 'confirm-undo',
         'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',
         'backstory-skip', 'backstory-keep-all', 'backstory-which-pick',
         'backstory-which-neither', 'reading-place', 'reading-no', 'reading-undo',
         'names-fixed-undo', 'spelling-yes', 'spelling-no'));

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
    """
    + READ_DOOR
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_place_spellings", "dmbot_web_place_spellings(BIGINT, TEXT)"),
)
