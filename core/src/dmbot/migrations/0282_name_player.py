"""0282_name_player

A name made a player character on the website, with the player's Discord ID if known (#2985).
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# The Names page's "What it is" could not say "player character": the bot refused it ("who plays
# them is asked in Discord"). The DM now picks it there, and may add the player's Discord ID (the
# 17 to 19 digits Discord's Developer Mode copies). The ID rides in the action's `text`, which a
# `kind` action had to leave empty; the door now allows it for `player_character` only, and only
# as digits. The bot checks the person is in the server and not a bot before it attaches them, as
# Discord's "Who plays it?" menu does. No table changes, no rows change.
_PLAYER_SWAPS = (
    (
        "(act IN ('kind', 'hide', 'merge', 'remove', 'link-secret',",
        "(act = 'kind' AND clean <> ''\n"
        "               AND (kd IS DISTINCT FROM 'player_character'\n"
        "                    OR clean !~ '^[1-9][0-9]{14,18}$'))\n"
        "           OR (act IN ('hide', 'merge', 'remove', 'link-secret',",
    ),
)

NAME_PLAYER = (
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
    + _swaps("def", _PLAYER_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = NAME_PLAYER
