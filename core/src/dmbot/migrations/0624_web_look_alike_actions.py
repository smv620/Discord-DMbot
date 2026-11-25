"""0624_web_look_alike_actions

The website's "Names that look alike" presses (#5259 part 1b): 🔗 Merge, Not the same and ↩ Undo on
a group, as Discord's group screen does (`ui/look_alikes.py`).
"""

from __future__ import annotations

# Three actions go on the queue door. `lookalike-merge` is filed under the kept name and its text
# holds the names merged into it (as `merge-many`'s does), then, after " | ", the group's names the
# DM left unticked, which the bot says are not the same as the kept one. `lookalike-apart` is filed
# under no name and holds the ids of the names that are not the same. `lookalike-undo` is filed
# under no name and holds a change number, which the bot reads again in the caller's own done
# merges before it undoes anything. The door checks only the shapes and (for a merge) that every
# merged name is a confirmed name that is not a player; the bot checks all of it again when it
# applies them. The lists are read back as they are, and each anchor must be found exactly the
# number of times given, as in 0615.
ACTIONS = ("lookalike-merge", "lookalike-apart", "lookalike-undo")
_IDS = "[0-9a-f]{32}( [0-9a-f]{32}){0,49}"
_OTHERS = "string_to_array(split_part(clean, ' | ', 1), ' ')"
_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'reveals-not')\n        THEN",
        "'reveals-not', 'lookalike-merge', 'lookalike-apart', 'lookalike-undo')\n        THEN",
        1,
    ),
    # the actions that take no flag
    (
        "'reveals-not')\n               AND COALESCE(fl, false))",
        "'reveals-not', 'lookalike-merge', 'lookalike-apart', 'lookalike-undo')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # the ones filed under no name (a merge is filed under the kept one)
    (
        "'reveals-not') THEN\n            -- a waiting connection",
        "'reveals-not', 'lookalike-apart', 'lookalike-undo') THEN\n"
        "            -- a waiting connection",
        1,
    ),
    # the text's shape
    (
        "           OR (act = 'names-more' AND clean <> '')\n",
        "           OR (act = 'names-more' AND clean <> '')\n"
        f"           OR (act = 'lookalike-merge' AND clean !~ '^{_IDS}( \\| {_IDS})?$')\n"
        f"           OR (act = 'lookalike-apart' AND clean !~ '^{_IDS}$')\n"
        "           OR (act = 'lookalike-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        1,
    ),
    # the longest text a merge may hold: as long as a merge of many names
    (
        "WHEN act IN ('merge-many', 'backstory-keep-all',",
        "WHEN act IN ('merge-many', 'lookalike-merge', 'backstory-keep-all',",
        1,
    ),
    # every merged name must be a confirmed name that is not a player, and not the kept one
    (
        "        IF act = 'map-control' AND clean <> '' AND NOT EXISTS (",
        "        IF act = 'lookalike-merge' AND (\n"
        f"               ent = ANY ({_OTHERS})\n"
        f"               OR (SELECT count(DISTINCT x) FROM unnest({_OTHERS}) x)\n"
        f"                  <> cardinality({_OTHERS})\n"
        "               OR (SELECT count(*) FROM memory_entities t\n"
        "                   WHERE t.guild_id = g AND t.campaign_id = c\n"
        f"                     AND t.id = ANY ({_OTHERS})\n"
        "                     AND t.status = 'confirmed' AND t.type <> 'person'\n"
        "                     AND NOT t.unnamed)\n"
        f"                  <> cardinality({_OTHERS})) THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_target');\n"
        "        END IF;\n"
        "        IF act = 'map-control' AND clean <> '' AND NOT EXISTS (",
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

    -- The table's own text limit: a lookalike merge may be as long as a merge of many names.
    DO $do$
    DECLARE
        d text;
        old text := $q$'merge-many'::text, $q$;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check '
            || replace(d, old, old || $q$'lookalike-merge'::text, $q$);
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
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
