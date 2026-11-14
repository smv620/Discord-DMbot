"""0358_players_line_door

The website writes and shows an entry's line for the players (#3568, the website half of #2967).
"""

from __future__ import annotations

# The All names read door (`dmbot_web_all_names`) sends each entry's `players_text` (core #3564)
# to a DM as `playersText`, null when there is none; anyone else gets null and never the value,
# because the line is the DM's to write and a player is told it only through recall. The queue
# door accepts one more action, `players-line`: the text is the line (a blank clears it, at most
# 120 letters, the column's own limit), no target, no flag; the bot runs it through
# `MemoryStore.set_players_text`.
# Both doors are read back from the database as they are (many migrations have changed them) and
# each anchor must be found the exact number of times, so a drift stops the migration instead of
# widening the wrong rule, as 0343 and 0354 do. No row is changed.
_ALL_NAMES_SWAPS = (
    # the column, carried through the visible rows
    (
        "SELECT e.id, e.name, e.created_at, e.unnamed, e.unnamed_what, e.unnamed_anchor,",
        "SELECT e.id, e.name, e.created_at, e.unnamed, e.unnamed_what, e.unnamed_anchor,\n"
        "                   e.players_text,",
        1,
    ),
    # a DM gets it, anyone else null
    (
        "'hidden', p.hidden, 'unnamed', p.unnamed, 'unnamedWhat', p.unnamed_what,",
        "'hidden', p.hidden, 'unnamed', p.unnamed, 'unnamedWhat', p.unnamed_what,\n"
        "                    'playersText', CASE WHEN is_dm THEN p.players_text END,",
        1,
    ),
)

_QUEUE_SWAPS = (
    # the allowed actions (with the line's own indent: the same words in the no-flag list differ)
    (
        "\n                'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no',",
        "\n                'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no',"
        " 'players-line',",
        1,
    ),
    # the line may be as long as the column allows
    (
        "THEN 1700 ELSE 100 END)",
        "THEN 1700 WHEN act = 'players-line' THEN 120 ELSE 100 END)",
        1,
    ),
    # no flag
    (
        "'link-words', 'link-who', 'alias-who',\n"
        "                       'confirm-kinds', 'confirm-undo')\n"
        "               AND COALESCE(fl, false))",
        "'link-words', 'link-who', 'alias-who',\n"
        "                       'confirm-kinds', 'confirm-undo', 'players-line')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _steps(swaps: tuple[tuple[str, str, int], ...]) -> str:
    return "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )


def _rebuild(door: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + door
        + r"""';"""
        + _steps(swaps)
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = (
    r"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other', 'secret',
         'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words', 'link-secret',
         'link-remove', 'link-pin', 'link-who', 'alias-who', 'confirm-kinds', 'confirm-undo',
         'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',
         'backstory-skip', 'backstory-keep-all', 'backstory-which-pick',
         'backstory-which-neither', 'reading-place', 'reading-no', 'reading-undo',
         'names-fixed-undo', 'spelling-yes', 'spelling-no', 'told-add', 'told-remove',
         'players-line'));
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check CHECK (char_length(text) <=
        CASE WHEN action IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who',
                             'confirm-kinds') THEN 1700
             WHEN action = 'players-line' THEN 120 ELSE 100 END);

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
    """
    + _rebuild("dmbot_web_all_names", _ALL_NAMES_SWAPS)
    + _rebuild("dmbot_web_name_action_add", _QUEUE_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
