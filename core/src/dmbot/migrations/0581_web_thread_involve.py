"""0581_web_thread_involve

The website's Plot threads panel can say who or what has a part in a thread (#3696, slice 2b;
Supervisor's ruling on slice 2): the 🧵 card's "👥 Add who's involved".
"""

from __future__ import annotations

# `thread-involve` is the queue door's new action: the text is `<thread id> <who's id> <part>`
# (the part is 1 to 60 characters of free words, spaces collapsed by the door; the store's
# `involve` refuses an empty part, #4937, so the website asks for one; the door's text cap of 100
# is 130 for this action alone). The bot runs
# the store's own `involve`, the call the card's form makes (one change; the card has no Undo for
# it, so the page has none). The queue door's lists, shape check and the table's action list are
# read back as they are and each anchor must be found exactly once. No table changes, no new door.
ACTIONS = ("thread-involve",)
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo', 'join-choices', 'join-choose', 'leave-yes', 'leave-no', "
    "'thread-state', 'thread-check'"
)
_NEXT = _PREV + ", 'thread-involve'"

_ID = "[0-9a-f]{32}"
_LAST_SHAPE = "           OR (act = 'thread-check' AND clean !~ '^" + _ID + "$')\n"
_CAP = "WHEN act = 'offer-clear' THEN 300 ELSE 100 END)"
_QUEUE_SWAPS = (
    # (the text holds two ids and the part, up to 126 characters: its cap is 130 for this action)
    (
        _CAP,
        "WHEN act = 'offer-clear' THEN 300 WHEN act = 'thread-involve' THEN 130 ELSE 100 END)",
        1,
    ),
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + f"           OR (act = 'thread-involve' AND clean !~ '^{_ID} {_ID} .{{1,60}}$')\n",
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

    -- The table's own text limit, read back as it is: a `thread-involve` text gets 130 (the door's
    -- function limits it first; this is the table's check beside it).
    DO $do$
    DECLARE
        d text;
        one text := ' WHEN (action = ''offer-clear''::text) THEN 300';
        more text := ' WHEN (action = ''thread-involve''::text) THEN 130';
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
