"""0583_web_thread_link

The website's Plot threads panel can link an event to a thread ("Moved the thread" or "Ended the
thread") and say what one thing led to (#3696, slice 2d; Supervisor's ruling on slice 2, and
GraphEngineer's answer there: a thread's event link is the Story Graph's own connection, but the
website still goes through `link_event`, so the thread rules, the DM check and the one change stay
the card's). The 🧵 card's "📌 Link an event" and "Led to…".
"""

from __future__ import annotations

# Two actions go on the queue door. `thread-link`: `<thread id> moved|ended <event id>`, or
# `<thread id> moved|ended new <event name>` to make the event on the spot as the card does (an
# event hidden from the players, like any name typed by hand, #2404); the bot runs the store's
# `link_event`. `thread-led-to`: `<from id> <to id>` (`led_to`, the DM's word, kept secret; the
# store refuses a loop). One change each (a made event is a change of its own), no Undo, as on the
# card. The text cap (100) is 160 for `thread-link`, in the door's function and in the table's own
# check. The queue door's lists, shape check and the table's action list are read back as they are
# and each anchor must be found exactly once. No table changes, no new door.
ACTIONS = ("thread-link", "thread-led-to")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo', 'join-choices', 'join-choose', 'leave-yes', 'leave-no', "
    "'thread-state', 'thread-check', 'thread-involve', 'thread-new', 'thread-due', 'thread-party'"
)
_NEXT = _PREV + ", 'thread-link', 'thread-led-to'"

_ID = "[0-9a-f]{32}"
_LAST_SHAPE = f"           OR (act = 'thread-party' AND clean !~ '^{_ID} (by|to) {_ID}$')\n"
_CAP = "WHEN act IN ('thread-new', 'thread-due') THEN 160 ELSE 100 END)"
_QUEUE_SWAPS = (
    # (`thread-link` with a new event's name: up to 143 characters)
    (
        _CAP,
        "WHEN act IN ('thread-new', 'thread-due', 'thread-link') THEN 160 ELSE 100 END)",
        1,
    ),
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'thread-link'\n"
        + f"               AND clean !~ '^{_ID} (moved|ended) ({_ID}|new .{{1,100}})$')\n"
        + f"           OR (act = 'thread-led-to' AND clean !~ '^{_ID} {_ID}$')\n",
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

    -- The table's own text limit, read back as it is: a `thread-link` text gets 160
    -- (the door's function limits it first; this is the table's check beside it).
    DO $do$
    DECLARE
        d text;
        one text := ' WHEN (action = ANY (ARRAY[''thread-new''::text, ''thread-due''::text]))'
                    ' THEN 160';
        more text := ' WHEN (action = ''thread-link''::text) THEN 160';
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
