"""0582_web_thread_new

The website's Plot threads panel can start a thread or a promise, set a promise's due note and
say who made it or who it was made to (#3696, slice 2c; Supervisor's ruling on slice 2): the 🧵
card's "New story thread", "Add a promise", "When is it due?", "Who promised?" and "Promised to".
"""

from __future__ import annotations

# Three actions go on the queue door. `thread-new`: `<kind> <state> <name>` (a kind of
# `memory.models.THREAD_TYPES`, a state of `memory.threads.ALL_STATES`, a name of 1 to 100
# characters); the bot runs the store's `new_thread` and the answer carries the new thread's id, so
# the page can follow with a due note for a promise (the card's form does the two calls one after
# the other as well). `thread-due`: `<promise id>[ <note>]`, at most 120 characters, empty clears
# it (`set_due`). `thread-party`: `<promise id> by|to <who's id>` (`promise_party`: who made it, or
# who it was made to). One change each, no Undo, as on the card. The door's text cap (100) is 160
# for `thread-new` and `thread-due`, in the function and in the table's own check. The queue
# door's lists, shape check and the table's action list are read back as they are and each anchor
# must be found exactly once. No table changes, no new door.
ACTIONS = ("thread-new", "thread-due", "thread-party")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo', 'join-choices', 'join-choose', 'leave-yes', 'leave-no', "
    "'thread-state', 'thread-check', 'thread-involve'"
)
_NEXT = _PREV + ", 'thread-new', 'thread-due', 'thread-party'"

_ID = "[0-9a-f]{32}"
_KINDS = "thread|quest|mystery|hook|goal|promise"
_STATES = "hinted|open|active|resolved|abandoned"
_LAST_SHAPE = f"           OR (act = 'thread-involve' AND clean !~ '^{_ID} {_ID} .{{1,60}}$')\n"
_CAP = "WHEN act = 'thread-involve' THEN 130 ELSE 100 END)"
_QUEUE_SWAPS = (
    # (a new thread's kind, state and name, or a promise's id and due note: up to 160 characters)
    (
        _CAP,
        "WHEN act = 'thread-involve' THEN 130 WHEN act IN ('thread-new', 'thread-due') THEN 160"
        " ELSE 100 END)",
        1,
    ),
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + f"           OR (act = 'thread-new' AND clean !~ '^({_KINDS}) ({_STATES}) .{{1,100}}$')\n"
        + f"           OR (act = 'thread-due' AND clean !~ '^{_ID}( .{{1,120}})?$')\n"
        + f"           OR (act = 'thread-party' AND clean !~ '^{_ID} (by|to) {_ID}$')\n",
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

    -- The table's own text limit, read back as it is: a `thread-new` or `thread-due` text gets 160
    -- (the door's function limits it first; this is the table's check beside it).
    DO $do$
    DECLARE
        d text;
        one text := ' WHEN (action = ''thread-involve''::text) THEN 130';
        more text := ' WHEN (action = ANY (ARRAY[''thread-new''::text, ''thread-due''::text]))'
                     ' THEN 160';
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, one, ''))) <> length(one) THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check '
            || replace(d, one, more || one);
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
