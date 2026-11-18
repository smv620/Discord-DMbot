"""0537_backstory_some_know

The website answers a waiting backstory connection with 👥 Some know (#4680 part 2, the web half of
#4534): one more action on the queue door, level with the card of Discord's own picker.
"""

from __future__ import annotations

# `backstory-keep-some` goes on the queue door as `backstory-keep-show` did (0499): its target is
# the waiting connection (the door's own check that every `backstory-` action needs) and its text
# is the characters who know it, ids joined by commas (1 to 25). The bot applies it through the
# keep Discord's picker makes: held back from the players, never secret, and exactly those
# characters know it, as the DM's own choice; one change, one Undo, as for any keep. It is refused
# for a connection with a secret name or a hand-over, as on the card. The queue door's lists, shape
# check and text limit are read back from the database as they are and each anchor must be found
# exactly the number of times given, so a drift stops the migration. No row changes.
ACTIONS = ("backstory-keep-some",)
_ID = "[0-9a-f]{32}"
_IDS = f"{_ID}(,{_ID}){{0,24}}"

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'claim-some', 'backstory-keep', 'backstory-keep-show', 'backstory-skip',"
        " 'backstory-keep-all',",
        "'claim-some', 'backstory-keep', 'backstory-keep-show', 'backstory-keep-some',"
        " 'backstory-skip', 'backstory-keep-all',",
        1,
    ),
    # both target lists (a waiting connection's id)
    (
        "'claim-some', 'backstory-keep', 'backstory-keep-show',"
        " 'backstory-skip',\n                       'backstory-which-pick'",
        "'claim-some', 'backstory-keep', 'backstory-keep-show', 'backstory-keep-some',"
        " 'backstory-skip',\n                       'backstory-which-pick'",
        2,
    ),
    # the connection must still wait (the door's own `not_found`)
    (
        "act IN ('backstory-keep', 'backstory-keep-show', 'backstory-skip') AND NOT EXISTS (",
        "act IN ('backstory-keep', 'backstory-keep-show', 'backstory-keep-some',"
        " 'backstory-skip') AND NOT EXISTS (",
        1,
    ),
    # the text's shape
    (
        f"           OR (act = 'claim-some' AND clean !~ '^{_IDS}$')\n",
        f"           OR (act = 'claim-some' AND clean !~ '^{_IDS}$')\n"
        f"           OR (act = 'backstory-keep-some' AND clean !~ '^{_IDS}$')\n",
        1,
    ),
    # no flag
    (
        "'claim-undo', 'claim-some', 'link-undo',",
        "'claim-undo', 'claim-some', 'backstory-keep-some', 'link-undo',",
        1,
    ),
    # the long text limit list in the door (the function's own check, beside the table's)
    (
        "'clue-add', 'claim-some')",
        "'clue-add', 'claim-some', 'backstory-keep-some')",
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

    -- The text limit, read back as it is: a `backstory-keep-some` text gets `part-add`'s limit.
    DO $do$
    DECLARE
        d text;
        fresh text;
        one text := ' WHEN (action = ''part-add''::text) THEN 1700';
        more text := ' WHEN (action = ''backstory-keep-some''::text) THEN 1700';
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, one, ''))) <> length(one) THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        fresh := replace(d, one, more || one);
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
