"""0315_confirm_kinds_actions

"Add all" for the names an upload found, on the website (#3207): the upload row keeps the names its
reading proposed, and two queued name actions, `confirm-kinds` and `confirm-undo`, say yes to them
(by kind or all at once) and take that back.
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# `found` is the reading's own answer, kind by kind: {"place": [ids], "npc": [ids]} (the kinds the
# reader gave them, as Discord's "Add all" buttons group them; ideas are listed but never added at
# once). Ids only, never a name or a word of the text; written by the statement that finishes the
# upload and kept with the row, which is cleaned up after a day. The bot answers a press from it,
# keeping only the names still waiting in this campaign, so a name answered since is left alone.
#
# `result_counts` is what a press did, as counts only ("place:5 npc:7"); `result_batches` are the
# memory changes it made, so one Undo can take back everything that press added.
_ACTION_SWAPS = (
    (
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        "                'link-who', 'alias-who',",
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        "                'link-who', 'alias-who', 'confirm-kinds', 'confirm-undo',",
    ),
    (
        "'backstory-keep-all', 'link-who', 'alias-who')",
        "'backstory-keep-all', 'link-who', 'alias-who', 'confirm-kinds')",
    ),
    # the text: the kinds to add (words, at most eight) or `all`; an Undo has none
    (
        "           OR (act IN ('link-who', 'alias-who')\n"
        "               AND clean !~ '^(dm|everyone|some( [0-9a-f]{32}){1,50})$')",
        "           OR (act IN ('link-who', 'alias-who')\n"
        "               AND clean !~ '^(dm|everyone|some( [0-9a-f]{32}){1,50})$')\n"
        "           OR (act = 'confirm-kinds'\n"
        "               AND clean !~ '^(all|[a-z_]{2,20}( [a-z_]{2,20}){0,7})$')\n"
        "           OR (act = 'confirm-kinds' AND ' ' || clean || ' ' LIKE '% idea %')\n"
        "           OR (act = 'confirm-kinds' AND clean <> 'all'\n"
        "               AND ' ' || clean || ' ' LIKE '% all %')\n"
        "           OR (act = 'confirm-undo' AND clean <> '')",
    ),
    # a target id for both, no flag
    (
        "           OR (act IN ('merge', 'link-who', 'alias-who', 'link-add', 'link-words',",
        "           OR (act IN ('merge', 'link-who', 'alias-who',\n"
        "                       'confirm-kinds', 'confirm-undo', 'link-add', 'link-words',",
    ),
    (
        "           OR (act NOT IN ('merge', 'link-who', 'alias-who', 'link-add', 'link-words',",
        "           OR (act NOT IN ('merge', 'link-who', 'alias-who',\n"
        "                           'confirm-kinds', 'confirm-undo', 'link-add', 'link-words',",
    ),
    (
        "           OR (act IN ('link-words', 'link-who', 'alias-who') AND COALESCE(fl, false))",
        "           OR (act IN ('link-words', 'link-who', 'alias-who',\n"
        "                       'confirm-kinds', 'confirm-undo')\n"
        "               AND COALESCE(fl, false))",
    ),
    # the target must be a finished upload of this campaign (confirm-kinds) or the same DM's own
    # finished press that made something (confirm-undo); neither is filed under a name
    (
        "        IF act = 'alias-who' AND NOT EXISTS (",
        "        IF act IN ('confirm-kinds', 'confirm-undo') THEN\n"
        "            subject := '';\n"
        "            IF act = 'confirm-kinds' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM lore_uploads u\n"
        "                   WHERE u.guild_id = g AND u.campaign_id = c AND u.id = tgt\n"
        "                     AND u.state = 'done') THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'confirm-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c AND p.id = tgt\n"
        "                     AND p.user_id = dmbot_current_user() AND p.action = 'confirm-kinds'\n"
        "                     AND p.state = 'done'\n"
        "                     AND cardinality(p.result_batches) > 0\n"
        "                     AND NOT EXISTS (SELECT 1 FROM name_actions u\n"
        "                                     WHERE u.guild_id = g AND u.campaign_id = c\n"
        "                                       AND u.action = 'confirm-undo'\n"
        "                                       AND u.target_id = tgt\n"
        "                                       AND u.state <> 'failed')) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            END IF;\n"
        "        END IF;\n"
        "        IF act = 'alias-who' AND NOT EXISTS (",
    ),
)

# The subject check below those branches would refuse an action with no name, so the two actions
# skip it the way the claim/backstory/reading ones do.
_SUBJECT_SWAPS = (
    (
        "        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%'\n"
        "           OR act LIKE 'names-fixed-%' OR act = 'name-undo' THEN",
        "        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%'\n"
        "           OR act LIKE 'names-fixed-%' OR act = 'name-undo'\n"
        "           OR act IN ('confirm-kinds', 'confirm-undo') THEN",
    ),
)

_READ_SWAPS = (
    (
        "'resultId', r.result_id,",
        "'counts', r.result_counts, 'resultId', r.result_id,",
    ),
)

# The upload's progress read also says how many names of each kind still wait, counts only, for
# the page's buttons: the names of the reading that are still proposed in this campaign.
_LORE_READ_SWAPS = (
    (
        "'createdAt', r.created_at, 'updatedAt', r.updated_at, 'serverNow', now_s);",
        "'createdAt', r.created_at, 'updatedAt', r.updated_at, 'serverNow', now_s,\n"
        "            'waiting', COALESCE((\n"
        "                SELECT jsonb_object_agg(f.key, n.cnt)\n"
        "                FROM jsonb_each(r.found) f\n"
        "                CROSS JOIN LATERAL (\n"
        "                    SELECT count(*) AS cnt FROM memory_entities e\n"
        "                    WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'proposed'\n"
        "                      AND e.id IN (SELECT jsonb_array_elements_text(\n"
        "                        CASE WHEN jsonb_typeof(f.value) = 'array' THEN f.value\n"
        "                             ELSE '[]'::jsonb END))) n\n"
        "                WHERE n.cnt > 0 AND f.key <> 'idea'), '{}'::jsonb));",
    ),
)

CONFIRM_KINDS_ACTIONS = (
    r"""
    ALTER TABLE lore_uploads
        ADD COLUMN found JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(found) = 'object' AND pg_column_size(found) <= 120000);
    ALTER TABLE name_actions
        ADD COLUMN result_counts TEXT NOT NULL DEFAULT '' CHECK (char_length(result_counts) <= 200),
        ADD COLUMN result_batches BIGINT[] NOT NULL DEFAULT '{}'
            CHECK (cardinality(result_batches) <= 50);

    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check CHECK (char_length(text) <=
        CASE WHEN action IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who',
                             'confirm-kinds') THEN 1700 ELSE 100 END);

    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other', 'secret',
         'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words', 'link-secret',
         'link-remove', 'link-pin', 'link-who', 'alias-who', 'confirm-kinds', 'confirm-undo',
         'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',
         'backstory-keep-all', 'backstory-which-pick', 'backstory-which-neither',
         'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo'));

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
    + _swaps("def", _ACTION_SWAPS + _SUBJECT_SWAPS)
    + r"""
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_read';
"""
    + _swaps("def", _READ_SWAPS)
    + r"""
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_lore_read';
"""
    + _swaps("def", _LORE_READ_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = CONFIRM_KINDS_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ()
