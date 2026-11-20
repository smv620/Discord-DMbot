"""0568_web_pack_picks

The website's Pick changes on a followed homebrew pack and its Undo (#4841 part C, the packs slice
P3; Supervisor's ruling on #4841: one handle table for sets and packs).
"""

from __future__ import annotations

# `pack-update-pick` carries `<the pack's id> <version> <picks>`: the pack's 32 hex digits, the
# version the DM was shown and a hex number whose bit i says change i of the shown list (as
# `pack_diff` gives it) is kept; at most 50 changes, so at most 13 hex digits. The bot takes the
# update with the store's own `take_pack_update`; leaving anything out ends the follow and copies
# the pack into the campaign's own homebrew, as Discord's Pick changes does. `pack-update-undo`
# carries the id of the undo handle the pick's answer gave. The handle is a row of the same
# `web_rule_takes` as a set's (one sweep, one catalog line, one backup line), told apart by the new
# `kind` ('set' or 'pack', default 'set', so every row there is a set's), and the newest handle is
# per kind. The queue door's lists, shape check and the table's action list are read back as they
# are and each anchor must be found exactly once, so a drift stops the migration.
ACTIONS = ("pack-update-pick", "pack-update-undo")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last'"
)
_NEXT = _PREV + ", 'pack-update-pick', 'pack-update-undo'"

_LAST_SHAPE = (
    "           OR (act = 'pack-update-undo-last' AND clean !~ '^[0-9a-f]{32} [0-9]{1,9}$')\n"
)
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'pack-update-pick'"
        + " AND clean !~ '^[0-9a-f]{32} [0-9]{1,9} [0-9a-f]{1,13}$')\n"
        + "           OR (act = 'pack-update-undo' AND clean !~ '^[0-9]{1,15}$')\n",
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
    + r"""
    ALTER TABLE web_rule_takes
        ADD COLUMN kind TEXT NOT NULL DEFAULT 'set' CHECK (kind IN ('set', 'pack'));
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
