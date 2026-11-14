"""0446_wizard_finish_channel

The wizard's finish state sends the campaign's DM Bot channel (#4219), so the last page can open it
in Discord.
"""

from __future__ import annotations

# The finish run makes the campaign's channels in its first stage; once the campaign exists, the DM
# Bot channel's id is on the campaign. The door already answers only the signed-in DM of that run
# (the run is theirs: `wizard_finishes.user_id`), so the id goes to the DM who made the campaign
# and to no one else; it is a Discord channel id, not a secret, and only their page shows it.
# `dmBotChannel` is the id as text, or null while no channel is made. The door is read back from
# the database as it is and the anchor must be found exactly once, so a drift stops the migration
# (the 0434 way). No row changes.
_OLD = "'movedAt', r.moved_at,"
_NEW = (
    "'dmBotChannel', (SELECT c.dm_bot_channel_id::TEXT FROM campaigns c\n"
    "                             WHERE c.guild_id = g AND c.id = r.made_campaign),\n"
    "            'movedAt', r.moved_at,"
)

SQL = (
    r"""
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
              AND p.proname = 'dmbot_web_wizard_finish_state';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD
    + r"""$q$, $q$"""
    + _NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
