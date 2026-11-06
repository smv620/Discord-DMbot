"""0355_web_told_actions

The website's "Tell a character" for a secret other name (#3542, the website half of #3089): two
queued name actions, `told-add` and `told-remove`, and a DM-only read door for the panel.
"""

from __future__ import annotations

# The other name is the action's target and the character's id rides in its `text` (one 32-letter
# id, nothing else). The door checks the sender is a DM, that the other name is a live one of the
# entry the action is filed under (a secret one to tell it, any live one to take the telling back),
# and that the character is a confirmed, unmerged character of this campaign. The bot checks the
# sender again and applies it with the same calls Discord's buttons use (`tell_character`,
# `take_back_told`). Nothing changes in any row here.
#
# The queue door is read back from the database as it is (many migrations have changed it) and each
# anchor must be found the exact number of times, so a drift stops the migration instead of widening
# the wrong rule, as 0354 does.
_SWAPS = (
    # the allowed actions
    (
        "'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        "'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no',\n"
        "                'told-add', 'told-remove')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        1,
    ),
    # both need a target (the other name) ...
    (
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n               AND (tgt IS NULL",
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no',\n"
        "                       'told-add', 'told-remove')\n               AND (tgt IS NULL",
        1,
    ),
    # ... and no other action takes one
    (
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no',\n"
        "                       'told-add', 'told-remove')\n"
        "               AND COALESCE(tgt, '') <> '')",
        1,
    ),
    # no flag
    (
        "'claim-pick', 'claim-unpick', 'name', 'name-join', 'name-undo', 'spelling-yes',"
        " 'spelling-no')\n               AND COALESCE(fl, false))",
        "'claim-pick', 'claim-unpick', 'name', 'name-join', 'name-undo', 'spelling-yes',"
        " 'spelling-no',\n                       'told-add', 'told-remove')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # the text is exactly one character's id
    (
        "           OR (act = 'backstory-which-pick' AND clean !~ '^[0-9a-f]{32}$')",
        "           OR (act = 'backstory-which-pick' AND clean !~ '^[0-9a-f]{32}$')\n"
        "           OR (act IN ('told-add', 'told-remove') AND clean !~ '^[0-9a-f]{32}$')",
        1,
    ),
    # the other name must be a live one of this entry (a secret one to tell it), and the character
    # a confirmed, unmerged character of this campaign
    (
        "        IF act IN ('link-who', 'alias-who') AND clean LIKE 'some %' AND (",
        "        IF act IN ('told-add', 'told-remove') AND NOT EXISTS (\n"
        "               SELECT 1 FROM memory_aliases a\n"
        "               WHERE a.guild_id = g AND a.campaign_id = c AND a.id = tgt\n"
        "                 AND a.entity_id = subject\n"
        "                 AND ((act = 'told-add' AND a.status = 'confirmed' AND a.secret)\n"
        "                      OR (act = 'told-remove' AND a.status <> 'rejected'))) THEN\n"
        "            RETURN jsonb_build_object('status', 'not_found');\n"
        "        END IF;\n"
        "        IF act IN ('told-add', 'told-remove') AND NOT EXISTS (\n"
        "               SELECT 1 FROM memory_entities t\n"
        "               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = clean\n"
        "                 AND t.type = 'character' AND t.status = 'confirmed'\n"
        "                 AND t.merged_into IS NULL) THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_target');\n"
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

# What the "Tell a character" panel reads (#3542): for each confirmed secret other name of a
# confirmed entry, the player characters who hold it and were not told (`holders`) and those
# already told (`told`), as id and name. "Holds it" is the store's own rule (a knower row of the
# character's view for the name that is not asleep, as in the bot), so a button is only offered
# where the bot will accept it. DM-only, worked out here: anyone else gets NULL. Names are shown
# to the DM only; nothing is written.
READ_DOOR = r"""
    CREATE FUNCTION dmbot_web_told_panel(g BIGINT, c TEXT) RETURNS JSONB
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
            SELECT jsonb_agg(row ORDER BY row->>'entryId', row->>'id')
            FROM (
                SELECT jsonb_build_object(
                    'id', a.id, 'entryId', a.entity_id,
                    'holders', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('id', x.id, 'name', x.name)
                                         ORDER BY x.name, x.id)
                        FROM memory_entities x
                        JOIN memory_perspectives v ON v.guild_id = x.guild_id
                             AND v.campaign_id = x.campaign_id AND v.entity_id = x.id
                        WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'
                          AND x.role = 'player_character' AND x.status = 'confirmed'
                          AND x.merged_into IS NULL
                          AND EXISTS (SELECT 1 FROM memory_knowers k
                                      WHERE k.guild_id = g AND k.campaign_id = c
                                        AND k.alias_id = a.id AND k.perspective_id = v.id
                                        AND cardinality(k.hidden_by) = 0)
                          AND NOT EXISTS (SELECT 1 FROM memory_told t
                                          WHERE t.guild_id = g AND t.campaign_id = c
                                            AND t.alias_id = a.id AND t.character_id = x.id)
                    ), '[]'::jsonb),
                    'told', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('id', x.id, 'name', x.name)
                                         ORDER BY x.name, x.id)
                        FROM memory_told t
                        JOIN memory_entities x ON x.guild_id = t.guild_id
                             AND x.campaign_id = t.campaign_id AND x.id = t.character_id
                        WHERE t.guild_id = g AND t.campaign_id = c AND t.alias_id = a.id
                          AND x.status = 'confirmed' AND x.merged_into IS NULL
                    ), '[]'::jsonb)) AS row
                FROM memory_aliases a
                JOIN memory_entities e ON e.guild_id = a.guild_id
                     AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c AND a.status = 'confirmed' AND a.secret
                  AND e.status = 'confirmed' AND e.type <> 'person' AND e.merged_into IS NULL
                LIMIT 500) r
            WHERE jsonb_array_length(row->'holders') + jsonb_array_length(row->'told') > 0
        ), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_told_panel(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_told_panel(BIGINT, TEXT) FROM PUBLIC;
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
         'names-fixed-undo', 'spelling-yes', 'spelling-no', 'told-add', 'told-remove'));

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
    ("dmbot_web_told_panel", "dmbot_web_told_panel(BIGINT, TEXT)"),
)
