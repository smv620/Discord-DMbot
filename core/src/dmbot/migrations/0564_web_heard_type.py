"""0564_web_heard_type

The website's "Did they mean…?", the "Type it…" answer (#4841 part B, slice B2; Supervisor's
ruling on #4841): the DM types the right name for a waiting question.
"""

from __future__ import annotations

# One more queue action, `heard-type`, with the text `<question id> <the name typed>`. The bot
# answers it with `DMBot.answer_waiting_coded(..., "type", ..., typed)`, the same function as
# Discord's form, which checks the name's rules (`typed-problem` and its reason), the DM, the
# speaker's consent and the campaign's secret names again. The door only checks the shape: the
# id, then at most as many characters as fit the queue's 100-character text limit (so a name that
# is too long reaches the bot, which says "longer than 60" in the page's own words, instead of a
# bare refusal), and an empty name is let through for the same reason. The door already trims and
# collapses spaces and refuses control characters. The typed words sit in the queue row only until
# the sweep after an hour, like any other typed name, and are never in an answer or a log. The
# queue door's lists, shape check and the table's action list are read back as they are and each
# anchor must be found exactly once. No table changes, no read door, no text-limit change.
ACTIONS = ("heard-type",)
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep'"
)
_NEXT = _PREV + ", 'heard-type'"

_LAST_SHAPE = "           OR (act = 'heard-keep' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'heard-type' AND clean !~ '^[0-9]{1,15}( .{1,84})?$')\n",
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
