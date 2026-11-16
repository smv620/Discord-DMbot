"""0515_thread_part_date

The website gives a story thread's time limit a game date, or clears it (#4503, quests slice 4a,
web half): one more action on the queue door, level with Discord's 📅 Set a date and 🗑️ Clear the
date.
"""

from __future__ import annotations

# `part-date`: `<part id> <days>` (the door checks the shape only, 1 to 9999; the bot takes 1 to
# 3650, `time_limits.MOST_DAYS`, and refuses more) or `<part id> -` to clear. Filed under no name,
# with no target and no flag, like 0514's actions. The page never sends a minute: the bot counts
# the days from the
# campaign's game clock when it applies the action (`time_limits.days_from`), so the website can
# never write a date the clock would not, and answers "no game clock" when there is none. The bot
# applies it through the store call Discord's card makes (`set_time_limit_date`): one change, one
# Undo (a `part-undo` takes it back). DM only (the door's own check, untouched). The queue door's
# lists are read back from the database as they are and each anchor must be found exactly once, so
# a drift stops the migration. No row is changed.
ACTION = "part-date"
_ID = "[0-9a-f]{32}"

_SHAPE = f"           OR (act = 'part-date' AND clean !~ '^{_ID} (-|[1-9][0-9]{{0,3}})$')\n"
_LIST = (
    "'part-add', 'part-fix', 'part-state', 'part-show', 'part-remove', 'branch-move',"
    " 'branch-not', 'part-undo'"
)
_NEW_LIST = _LIST + ", 'part-date'"

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (f"{_LIST})\n        THEN", f"{_NEW_LIST})\n        THEN", 1),
    # their shape, after the Undo's of 0514
    (
        "           OR (act = 'part-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'part-undo' AND clean !~ '^[0-9]{1,15}$')\n" + _SHAPE,
        1,
    ),
    # no flag
    (
        f"{_LIST})\n               AND COALESCE(fl, false))",
        f"{_NEW_LIST})\n               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (f"{_LIST}) THEN", f"{_NEW_LIST}) THEN", 1),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

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
            || replace(d, '])', ', ''part-date''::text])');
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
