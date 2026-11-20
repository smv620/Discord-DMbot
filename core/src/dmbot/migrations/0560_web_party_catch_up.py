"""0560_web_party_catch_up

The website's "Catch up" on an entry's page (#4793, party knowledge slice 4b; Supervisor's ruling on
#4537): ask who is missing what on a card, and give one party member what the rest know there.
"""

from __future__ import annotations

# Both go on the queue door as the join question's did: filed under no name, no target, no flag, and
# the bot answers them, so the who-knows rule stays in the one place that has it
# (`knowers.tell_counts` and `knowers.tell_member`, through the store's `tell_choices` and
# `tell_member`) and none of it is copied into SQL. `party-catch-up-choices` carries the entry's id;
# its answer lists each member with something to catch up on, as ids and counts only.
# `party-catch-up` carries `<member> <entry>` (two ids); the bot checks the member is in the party
# now, and an Undo only of a change this DM's own done answer made (the change number rides
# `party-undo`, as for the party record). The
# queue door's lists, shape check and the table's action list are read back as they are and each
# anchor must be found exactly once, so a drift stops the migration. No row is changed.
ACTIONS = ("party-catch-up-choices", "party-catch-up")
_ID = "[0-9a-f]{32}"
_PREV = "'join-undo', 'party-leave', 'party-back', 'party-undo'"
_NEXT = _PREV + ", 'party-catch-up-choices', 'party-catch-up'"

_LAST_SHAPE = "           OR (act = 'party-undo' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + f"           OR (act = 'party-catch-up-choices' AND clean !~ '^{_ID}$')\n"
        + f"           OR (act = 'party-catch-up' AND clean !~ '^{_ID} {_ID}$')\n",
        1,
    ),
    (
        f"{_PREV})\n               AND COALESCE(fl, false))",
        f"{_NEXT})\n               AND COALESCE(fl, false))",
        1,
    ),
    (f"{_PREV}) THEN", f"{_NEXT}) THEN", 1),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)

SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and one more added at the end.
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
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
