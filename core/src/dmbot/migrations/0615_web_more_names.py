"""0615_web_more_names

The website's "📝 More names" press (#5081, the website half of #5055 B): a DM of the campaign asks
the bot to turn the names an imported session's scan heard past its first 10 into names to check,
as Discord's button does (`DMBot.add_more_names`).
"""

from __future__ import annotations

# One action goes on the queue door, `names-more`: filed under no name, no target, no flag and no
# text (the door checks only that shape; the bot checks the rest again: a DM of the campaign). One
# press makes at most 300 names and says how many it made and how many still wait; it makes no
# change the page can undo, as on Discord. The queue door's lists and shape check and the table's
# action list are read back as they are and each anchor must be found exactly the number of times
# given (0605's lists are the ones extended). No new table and no new door.
ACTIONS = ("names-more",)
_QUEUE_SWAPS = (
    # the allowed actions, the actions without a flag, and the ones filed under no name
    (
        "'continuity-ask', 'continuity-answer', 'route-length'",
        "'continuity-ask', 'continuity-answer', 'names-more', 'route-length'",
        3,
    ),
    # the text's shape: none
    (
        "           OR (act = 'continuity-ask' AND clean <> '')\n",
        "           OR (act = 'continuity-ask' AND clean <> '')\n"
        "           OR (act = 'names-more' AND clean <> '')\n",
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
