"""0514_thread_part_actions

The website changes a kept story thread's parts and branches (#4476, quests slice 2, web half): the
queue door takes eight more actions, level with Discord's 🧵 "📋 Steps, rewards and more".
"""

from __future__ import annotations

# The actions go on the queue door as 0505's did, filed under no name, with no target and no flag;
# the door checks only their shape, the bot applies each through the store call Discord's card makes
# (`add_thread_part`, `correct_thread_part`, `set_thread_part_state`, `show_thread_part`,
# `remove_thread_part`, `branch_of`, `not_a_branch`), each one change with one Undo, and checks the
# rest again (a part or thread gone meanwhile is answered "gone"). All are DM only (the door's own
# check, untouched). Ids are 32 hex characters; `-` means "none" or "clear".
# - `part-add`: `<thread> <kind> <state|-> <due|-> <limit|-> <who|-> <about|-> <under|-> <words>`
#   (words 1 to 200 characters);
# - `part-fix`: `<part> words|words_story|terms <text>`, `<part> who|about|under <id|->`,
#   `<part> due <up_front|afterwards|->`, `<part> limit <soft|hard|->` (`words_story`: a change in
#   the story, the old words kept as history, as Discord asks);
# - `part-state`: `<part> <state word>`; `part-show`: `<part> 0|1`; `part-remove`: `<part>`;
# - `branch-move`: `<branch> <bigger thread>` and optionally ` move`; `branch-not`: `<thread>`;
# - `part-undo`: the change number one of these made, checked by the bot against the DM's own
#   done actions.
# `part-add` and `part-fix` get the long text limit, in the door and in the table's check. The queue
# door and its lists are read back from the database as they are and each anchor must be found
# exactly once, so a drift stops the migration. No row is changed.
ACTIONS = (
    "part-add", "part-fix", "part-state", "part-show", "part-remove",
    "branch-move", "branch-not", "part-undo",
)  # fmt: skip
LONG_TEXT = ("part-add", "part-fix")
_ID = "[0-9a-f]{32}"
_OPT_ID = f"(-|{_ID})"
_KINDS = "(objective|complication|reward|reward_given|help|time_limit)"

_SHAPES = (
    f"           OR (act = 'part-add' AND clean !~ '^{_ID} {_KINDS} (-|[a-z_]{{1,20}})"
    f" (-|up_front|afterwards) (-|soft|hard) {_OPT_ID} {_OPT_ID} {_OPT_ID} [^ ].{{0,199}}$')\n"
    f"           OR (act = 'part-fix' AND clean !~ '^{_ID}"
    f" ((words|words_story|terms) [^ ].{{0,199}}"
    f"|(who|about|under) {_OPT_ID}|due (-|up_front|afterwards)|limit (-|soft|hard))$')\n"
    f"           OR (act = 'part-state' AND clean !~ '^{_ID} [a-z_]{{1,20}}$')\n"
    f"           OR (act = 'part-show' AND clean !~ '^{_ID} [01]$')\n"
    f"           OR (act = 'part-remove' AND clean !~ '^{_ID}$')\n"
    f"           OR (act = 'branch-move' AND clean !~ '^{_ID} {_ID}( move)?$')\n"
    f"           OR (act = 'branch-not' AND clean !~ '^{_ID}$')\n"
    f"           OR (act = 'part-undo' AND clean !~ '^[0-9]{{1,15}}$')\n"
)
_LIST = "'maps-add', 'thread-keep', 'thread-not', 'thread-undo'"
_NEW_LIST = _LIST + ", " + ", ".join(f"'{a}'" for a in ACTIONS)

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (f"{_LIST})\n        THEN", f"{_NEW_LIST})\n        THEN", 1),
    # their shape, after the maps Undo's (0505's lines follow it and stay)
    (
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n" + _SHAPES,
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
    # the long text limit list in the door
    (
        "'alias-who', 'confirm-kinds', 'world-keep', 'maps-add', 'thread-keep')",
        "'alias-who', 'confirm-kinds', 'world-keep', 'maps-add', 'thread-keep', 'part-add',"
        " 'part-fix')",
        1,
    ),
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
    -- The allowed actions, read back from the table as it is and eight more added at the end.
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

    -- The text limit, read back as it is: a `part-add` or `part-fix` text gets the long limit. The
    -- new cases go before the players-line case, which must be there exactly once (the length
    -- check).
    DO $do$
    DECLARE
        d text;
        fresh text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        fresh := replace(d, 'WHEN (action = ''players-line''::text) THEN 120',
                         'WHEN (action = ''part-add''::text) THEN 1700'
                         || ' WHEN (action = ''part-fix''::text) THEN 1700'
                         || ' WHEN (action = ''players-line''::text) THEN 120');
        IF fresh = d OR (length(fresh) - length(d))
                        <> length(' WHEN (action = ''part-add''::text) THEN 1700'
                                  || ' WHEN (action = ''part-fix''::text) THEN 1700') THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check ' || fresh;
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
