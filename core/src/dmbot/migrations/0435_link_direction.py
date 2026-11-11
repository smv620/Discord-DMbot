"""0435_link_direction

A connection's direction, set by the DM: A → B, B → A or A ↔ B (#4154).
"""

from __future__ import annotations

# `both_ways` on a connection: NULL reads as its kind's default (`link_words.BOTH_WAYS`: rivals,
# friends, enemies, family read both ways), true makes it read both ways, false one way. Only the
# DM sets it, through the store's `set_direction`, whose change log gives Undo; a flip (A → B to
# B → A) swaps the two names in place there and needs nothing here. No existing row changes (NULL
# keeps every connection as it reads today), so no row-level security needs opening.
#
# The website asks for it with a new queued action, 'link-direction' (text "a-b", "b-a" or "both",
# never a flag, on a confirmed connection like the other link edits). The action list and the door
# are read back as they are, the one addition made at each place, and each place must be found
# the exact number of times, so a drift stops the migration instead of changing the wrong line
# (as 0372 and 0400 do). It runs after 0434_link_undo and extends the lists as that one leaves
# them. The two graph doors carry the column on each connection, so a viewer is
# told it only for a connection the door or the gate already shows them.
_DOOR_SWAPS = (
    # the actions the door takes
    (
        "'merge-many', 'remove',\n                'link-add', 'link-words', 'link-secret',"
        " 'link-remove', 'link-pin', 'link-undo',",
        "'merge-many', 'remove',\n                'link-add', 'link-words', 'link-secret',"
        " 'link-remove', 'link-pin', 'link-undo', 'link-direction',",
        1,
    ),
    # the ones that name a connection as their target (and only they do)
    (
        "'confirm-undo', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',"
        " 'link-undo',",
        "'confirm-undo', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',"
        " 'link-undo', 'link-direction',",
        2,
    ),
    # its text is one of the three directions, and it carries no flag
    (
        "           OR (act = 'claim-undo' AND clean !~ '^[0-9]{1,12}$')",
        "           OR (act = 'claim-undo' AND clean !~ '^[0-9]{1,12}$')\n"
        "           OR (act = 'link-direction'\n"
        "               AND (clean NOT IN ('a-b', 'b-a', 'both') OR COALESCE(fl, false)))",
        1,
    ),
)
_SOURCE_SWAPS = (
    ("'knows_how', r.knows_how,", "'knows_how', r.knows_how, 'both_ways', r.both_ways,", 1),
)
_RELATIONSHIPS_SWAPS = (
    (
        "'directional', r.directional, 'knows_how', r.knows_how",
        "'directional', r.directional, 'knows_how', r.knows_how, 'both_ways', r.both_ways",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _swap(function: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    """One DO block: `function` read back as it is, each swap made, and made again (its settings
    come back with `pg_get_functiondef`, and CREATE OR REPLACE keeps its grants)."""
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
        + function
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
"""
    )


SQL = (
    r"""
    ALTER TABLE memory_relations ADD COLUMN both_ways BOOLEAN;

    -- The allowed actions, read back from the table as it is and one more added at the end, so an
    -- action another migration added stays in the list whatever order the migrations merge in.
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
            || replace(d, '])', ', ''link-direction''::text])');
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
    + _swap("dmbot_web_name_action_add", _DOOR_SWAPS)
    + _swap("dmbot_web_graph_source", _SOURCE_SWAPS)
    + _swap("dmbot_web_relationships", _RELATIONSHIPS_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
