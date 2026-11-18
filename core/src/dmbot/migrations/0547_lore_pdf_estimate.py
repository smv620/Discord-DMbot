"""0547_lore_pdf_estimate

The bot counts a staged lore PDF's pages and pictures before the DM's yes (#4762, step 2).
"""

from __future__ import annotations

# `lore-pdf-estimate` goes on the queue door as the clue actions of 0533 did: filed under no name,
# with no target and no flag. Its text is the staged file's id (32 hex), so a press made on an
# older file than the one now staged finds nothing to count. The door checks only the shape and
# that the sender is a DM, as it does for every action; the bot checks the sender again, reads
# the staged object, counts and writes the three numbers back (`web_actions`, `lore_pdfs`). The
# queue door's lists, shape check and the table's action list are read back from the database as
# they are and each anchor must be found exactly once, so a drift stops the migration. No row is
# changed.
ACTIONS = ("lore-pdf-estimate",)
_ID = "[0-9a-f]{32}"

_LAST_SHAPE = "           OR (act = 'clue-undo' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (
        "'clue-add', 'clue-word', 'clue-off', 'clue-undo')\n        THEN",
        "'clue-add', 'clue-word', 'clue-off', 'clue-undo', 'lore-pdf-estimate')\n        THEN",
        1,
    ),
    # its shape
    (
        _LAST_SHAPE,
        _LAST_SHAPE + f"           OR (act = 'lore-pdf-estimate' AND clean !~ '^{_ID}$')\n",
        1,
    ),
    # no flag
    (
        "'clue-add', 'clue-word', 'clue-off', 'clue-undo')\n"
        "               AND COALESCE(fl, false))",
        "'clue-add', 'clue-word', 'clue-off', 'clue-undo', 'lore-pdf-estimate')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (
        "'clue-add', 'clue-word', 'clue-off', 'clue-undo') THEN",
        "'clue-add', 'clue-word', 'clue-off', 'clue-undo', 'lore-pdf-estimate') THEN",
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
