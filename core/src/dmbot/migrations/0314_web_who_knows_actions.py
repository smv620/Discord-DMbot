"""0314_web_who_knows_actions

The website's "Who knows this?" for a connection and an other name (#2999, #2935 D2): two queued
name actions, `link-who` and `alias-who`.
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# The answer rides in the action's `text`, as the lists of ids of `merge-many` do: exactly `dm`
# (only the DM), `everyone`, or `some` and then one to fifty distinct character ids separated by
# spaces (the characters who know it: player characters and NPCs, §9b). `target_id` is the
# connection (`link-who`) or the other name's id (`alias-who`, filed under the name it belongs to,
# the action's entity). The door checks the sender is a DM, that the connection or other name is
# a live one of this campaign, and that every character is a confirmed, unmerged character or
# group of this campaign. The bot checks the sender again and applies it with the same call
# Discord's buttons use (CloudDev's half). Nothing changes in any row.
_WHO_SWAPS = (
    # the allowed actions
    (
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        "                'claim-confirm', 'claim-reject', 'backstory-keep',",
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        "                'link-who', 'alias-who',\n"
        "                'claim-confirm', 'claim-reject', 'backstory-keep',",
    ),
    # a longer text for the character lists
    (
        "(CASE WHEN act IN ('merge-many', 'backstory-keep-all') THEN 1700 ELSE 100 END)",
        "(CASE WHEN act IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who')\n"
        "                                          THEN 1700 ELSE 100 END)",
    ),
    # the text must be one of the three answers
    (
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ "
        "'^[0-9a-f]{32}( [0-9a-f]{32}){0,49}$'))",
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ "
        "'^[0-9a-f]{32}( [0-9a-f]{32}){0,49}$'))\n"
        "           OR (act IN ('link-who', 'alias-who')\n"
        "               AND clean !~ '^(dm|everyone|some( [0-9a-f]{32}){1,50})$')",
    ),
    # a target id, as a connection's other edits have
    (
        "           OR (act IN ('merge', 'link-add', 'link-words',",
        "           OR (act IN ('merge', 'link-who', 'alias-who', 'link-add', 'link-words',",
    ),
    (
        "           OR (act NOT IN ('merge', 'link-add', 'link-words',",
        "           OR (act NOT IN ('merge', 'link-who', 'alias-who', 'link-add', 'link-words',",
    ),
    # no flag
    (
        "           OR (act = 'link-words' AND COALESCE(fl, false))",
        "           OR (act IN ('link-words', 'link-who', 'alias-who') AND COALESCE(fl, false))",
    ),
    # an other name must be one of the entry's own, live ones; the characters must all be
    # confirmed characters or groups of this campaign, unmerged, and listed once each
    (
        "        IF act IN ('merge', 'link-add', 'name-join') AND (tgt = subject OR NOT EXISTS (",
        "        IF act = 'alias-who' AND NOT EXISTS (\n"
        "               SELECT 1 FROM memory_aliases a\n"
        "               WHERE a.guild_id = g AND a.campaign_id = c AND a.id = tgt\n"
        "                 AND a.entity_id = subject AND a.status <> 'rejected') THEN\n"
        "            RETURN jsonb_build_object('status', 'not_found');\n"
        "        END IF;\n"
        "        IF act IN ('link-who', 'alias-who') AND clean LIKE 'some %' AND (\n"
        "               (SELECT count(DISTINCT x)\n"
        "                FROM unnest(string_to_array(substr(clean, 6), ' ')) x)\n"
        "                  <> cardinality(string_to_array(substr(clean, 6), ' '))\n"
        "               OR (SELECT count(*) FROM memory_entities t\n"
        "                   WHERE t.guild_id = g AND t.campaign_id = c\n"
        "                     AND t.id = ANY (string_to_array(substr(clean, 6), ' '))\n"
        "                     AND t.type IN ('character', 'faction')\n"
        "                     AND t.status = 'confirmed' AND t.merged_into IS NULL)\n"
        "                  <> cardinality(string_to_array(substr(clean, 6), ' '))) THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_target');\n"
        "        END IF;\n"
        "        IF act IN ('merge', 'link-add', 'name-join') AND (tgt = subject OR NOT EXISTS (",
    ),
)

WEB_WHO_KNOWS_ACTIONS = (
    r"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other', 'secret',
         'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words', 'link-secret',
         'link-remove', 'link-pin', 'link-who', 'alias-who', 'claim-confirm', 'claim-reject',
         'backstory-keep', 'backstory-skip', 'backstory-keep-all', 'backstory-which-pick',
         'backstory-which-neither', 'reading-place', 'reading-no', 'reading-undo',
         'names-fixed-undo'));
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check CHECK (char_length(text) <=
        CASE WHEN action IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who')
             THEN 1700 ELSE 100 END);

    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
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
              AND p.proname = 'dmbot_web_name_action_add';
"""
    + _swaps("def", _WHO_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = WEB_WHO_KNOWS_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ()
