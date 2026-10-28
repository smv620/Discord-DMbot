"""0266_web_name_unnamed

"Not named yet" on the Names page (#2803).
"""

from __future__ import annotations


# "Not named yet" on the website (#2803): a DM's Names page lists the people the story described and
# never named (0263), and gives them a name through the queue the other name actions use. Three
# new actions: `name` (a new name for them: the bot renames, or answers `known_name` with the one
# DMbot already knows, or `ambiguous` / `gone`), `name-join` (the DM said "same person": the bot
# joins them into the named one, keeping it) and `name-undo` (the done one of those two, undone
# with the batch the bot kept in `result_other`). The two doors and the action list are read back
# from the database as they are now and widened, one place at a time, each of which must be found
# exactly once: a door whose text changed under this migration stops it instead of passing
# silently. A confirmed unnamed person never takes the other actions (a spelling, a kind, a secret):
# those are for named entries.
def _swaps(var: str, pairs: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(
        f"        {var} := pg_temp.swap_once({var}, $o${old}$o$, $n${new}$n$);"
        for old, new in pairs
    )


_NAME_DOOR_SWAPS = (
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n        THEN",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                'name', 'name-join', 'name-undo')\n        THEN",
    ),
    (
        "(act IN ('spell', 'add-other', 'remove-other', 'secret', 'link-add', 'link-words')\n"
        "               AND clean = '')",
        "(act IN ('spell', 'add-other', 'remove-other', 'secret', 'link-add', 'link-words',\n"
        "                   'name')\n"
        "               AND clean = '')",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND clean <> '')",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name-join', 'name-undo')\n               AND clean <> '')",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND (tgt IS NULL OR",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name-join', 'name-undo')\n               AND (tgt IS NULL OR",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name-join', 'name-undo')\n"
        "               AND COALESCE(tgt, '') <> '')",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND COALESCE(fl, false))",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name', 'name-join', 'name-undo')\n"
        "               AND COALESCE(fl, false))",
    ),
    (
        "OR act LIKE 'names-fixed-%' THEN",
        "OR act LIKE 'names-fixed-%' OR act = 'name-undo' THEN",
    ),
    (
        "ELSIF act = 'reading-undo' AND NOT EXISTS (",
        "ELSIF act = 'name-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c\n"
        "                   AND p.id = tgt AND p.user_id = dmbot_current_user()\n"
        "                   AND p.action IN ('name', 'name-join') AND p.state = 'done'\n"
        "                   AND p.result_other <> '') THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (",
    ),
    (
        "                 AND e.status = 'confirmed' AND e.type <> 'person') THEN",
        "                 AND ((e.status = 'confirmed' AND e.type <> 'person' AND NOT e.unnamed\n"
        "                       AND act NOT IN ('name', 'name-join'))\n"
        "                      OR (act IN ('name', 'name-join') AND e.unnamed\n"
        "                          AND e.status IN ('proposed', 'confirmed')))) THEN",
    ),
    (
        "IF act IN ('merge', 'link-add') AND (tgt = subject OR NOT EXISTS (",
        "IF act IN ('merge', 'link-add', 'name-join') AND (tgt = subject OR NOT EXISTS (",
    ),
    (
        "                 AND t.status = 'confirmed' AND t.type <> 'person')) THEN",
        "                 AND t.status = 'confirmed' AND t.type <> 'person'\n"
        "                 AND NOT t.unnamed)) THEN",
    ),
    (
        "                     AND t.status = 'confirmed' AND t.type <> 'person')\n"
        "                  <> cardinality(",
        "                     AND t.status = 'confirmed' AND t.type <> 'person'\n"
        "                     AND NOT t.unnamed)\n"
        "                  <> cardinality(",
    ),
)

_NAMES_PAGE_SWAPS = (
    (
        "SELECT e.id, e.name, e.created_at,\n",
        "SELECT e.id, e.name, e.created_at, e.unnamed, e.unnamed_what, e.unnamed_anchor,\n",
    ),
    (
        "CASE WHEN e.type = 'character' THEN",
        "CASE WHEN e.unnamed THEN 'not_named' WHEN e.type = 'character' THEN",
    ),
    (
        "              AND e.status = 'confirmed' AND e.type <> 'person'\n",
        "              AND ((e.status = 'confirmed' AND e.type <> 'person')\n"
        "                   OR (is_dm AND e.unnamed AND e.status IN ('proposed', 'confirmed')\n"
        "                       AND COALESCE(current_setting('dmbot.names_unnamed', true), '')\n"
        "                           = '1'))\n",
    ),
    (
        "'hidden', p.hidden,",
        "'hidden', p.hidden, 'unnamed', p.unnamed, 'unnamedWhat', p.unnamed_what,\n"
        "                    'unnamedAnchor', (\n"
        "                        SELECT jsonb_build_object(\n"
        "                            'name', x.name, 'what', x.unnamed_what,\n"
        "                            'anchorName', y.name)\n"
        "                        FROM memory_entities x\n"
        "                        LEFT JOIN memory_entities y ON y.guild_id = x.guild_id\n"
        "                             AND y.campaign_id = x.campaign_id\n"
        "                             AND y.id = x.unnamed_anchor\n"
        "                        WHERE x.guild_id = g AND x.campaign_id = c\n"
        "                          AND x.id = p.unnamed_anchor),",
    ),
)

WEB_NAME_UNNAMED = (
    r"""
    CREATE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
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
        con text;
    BEGIN
        SELECT pg_get_constraintdef(c.oid) INTO con FROM pg_constraint c
            WHERE c.conrelid = 'name_actions'::regclass AND c.conname = 'name_actions_action_check';
        IF con IS NULL OR position($q$'spell'::text,$q$ IN con) = 0 THEN
            RAISE EXCEPTION 'the name action list changed under this migration';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(
                con, $q$'spell'::text,$q$,
                $q$'spell'::text, 'name'::text, 'name-join'::text, 'name-undo'::text,$q$);

        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';
"""
    + _swaps("def", _NAME_DOOR_SWAPS)
    + r"""
        EXECUTE def;

        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_all_names';
"""
    + _swaps("def", _NAMES_PAGE_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
"""
)

SQL = WEB_NAME_UNNAMED
