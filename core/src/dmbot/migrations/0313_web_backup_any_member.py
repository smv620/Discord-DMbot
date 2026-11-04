"""0313_web_backup_any_member

Anyone in the campaign's server may ask the website for a copy of a campaign, not only a game
master (#3148). The bot's half (#3150) already builds one for any member.
"""

from __future__ import annotations

# CLAUDE.md, "Backups": anyone in the server may download a backup of a paid campaign. The door
# let in only the campaign's owner and its game masters, so a player saw no way to get a copy.
# It now lets in the owner and anyone whose sign-in lists the campaign's server. The plan check
# (Try It campaigns have no backups) stays with the bot, which says so in the row, as before.
# The function is rewritten in place from its stored text (so its pinned search path, its
# SECURITY DEFINER and its grants stay exactly as they are), and the swap must match once: a door
# that was changed since this was written stops the migration instead of being half-edited.
_OLD_GUARD = """\
            IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                           WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me)
               OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
"""
_NEW_GUARD = """\
            IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
"""

WEB_BACKUP_ANY_MEMBER = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap(def text, old text, new text, lo int, hi int)
    RETURNS text LANGUAGE plpgsql AS $f$
    DECLARE
        n int := (length(def) - length(replace(def, old, ''))) / greatest(length(old), 1);
    BEGIN
        IF n < lo OR n > hi THEN
            RAISE EXCEPTION 'found % times, wanted % to %: %', n, lo, hi, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        def := pg_get_functiondef('dmbot_web_backup_add(bigint, text)'::regprocedure);
        def := pg_temp.swap(def, $old$"""
    + _OLD_GUARD
    + r"""$old$, $new$"""
    + _NEW_GUARD
    + r"""$new$, 1, 1);
        EXECUTE def;
    END
    $do$;
    """
)

SQL = WEB_BACKUP_ANY_MEMBER

ISOLATED_TABLES: tuple[str, ...] = ()
