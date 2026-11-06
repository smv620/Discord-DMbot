"""0285_name_player_lookup

Ask Discord who an ID is before a player character is linked to it (#3004).
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# 0282 let a `kind` action carry a Discord ID for `player_character`. The owner wants the ID
# confirmed by name first, so the page asks the bot who the ID is (a `kind` action whose kind is
# `player_lookup`: the bot asks Discord and writes nothing) and saves only after the DM says "That's
# them". The same digits-only rule now holds for both kinds; the door's other rules are
# unchanged. No table changes, no rows change.
_LOOKUP_SWAPS = (
    (
        "kd IS DISTINCT FROM 'player_character'",
        "kd NOT IN ('player_character', 'player_lookup')",
    ),
)

NAME_PLAYER_LOOKUP = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
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
              AND p.proname = 'dmbot_web_name_action_add';
"""
    + _swaps("def", _LOOKUP_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = NAME_PLAYER_LOOKUP
