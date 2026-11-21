"""0580_web_thread_state

The website's Plot threads panel can change a thread's state (#3696, slice 2a; Supervisor's ruling
on slice 2): "Happening now", "Hinted"… and "Checked, still going", the card's own two writes.
"""

from __future__ import annotations

# `thread-state` (`<thread id> <state>`, a state word of `memory.threads.ALL_STATES`) and
# `thread-check` (`<thread id>`) are the queue door's two new actions; the bot runs the store's own
# `set_condition` and `check_thread`, the calls the 🧵 card's buttons make (one change each, as
# there; the card has no Undo for either, so the page has none). The queue door's lists, shape
# check and the table's action list are read back as they are and each anchor must be found
# exactly once. No table changes and no new door: the Plot threads read door is 0576.
ACTIONS = ("thread-state", "thread-check")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo', 'join-choices', 'join-choose', 'leave-yes', 'leave-no'"
)
_NEXT = _PREV + ", 'thread-state', 'thread-check'"

_ID = "[0-9a-f]{32}"
_STATES = "hinted|open|active|resolved|abandoned"
_LAST_SHAPE = "           OR (act IN ('leave-yes', 'leave-no') AND clean !~ '^" + _ID + "$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + f"           OR (act = 'thread-state' AND clean !~ '^{_ID} ({_STATES})$')\n"
        + f"           OR (act = 'thread-check' AND clean !~ '^{_ID}$')\n",
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
