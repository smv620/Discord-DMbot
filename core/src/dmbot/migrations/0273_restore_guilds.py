"""0273_restore_guilds

Servers with restore rows, for the sweep (#2746).
"""

from __future__ import annotations

# The servers that have website-restore rows (#2746): `web_restores` is isolated per server, so a
# sweep that must reach a server the bot has left (its rows hold whole campaigns) cannot list them
# itself. A routing table like `campaign_guilds`: server ids and nothing else, so no row-level
# security (see the rules at the top of this file). A trigger fills it when a row is staged, and
# the bot's upkeep removes a server once it has no rows left: it locks the server's row first
# (`SELECT … FOR UPDATE`), then runs the delete as a second statement, so the check sees every row
# committed by then; an insert staged meanwhile waits on the same lock (the trigger's conflict
# clause takes it) and puts the row back. (`web_restores` is new and empty
# everywhere, so there is nothing to backfill.)
WEB_RESTORE_GUILDS = """
    CREATE TABLE web_restore_guilds (
        guild_id BIGINT PRIMARY KEY
    );
    CREATE FUNCTION dmbot_note_restore_guild() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        INSERT INTO web_restore_guilds (guild_id) VALUES (NEW.guild_id)
            ON CONFLICT (guild_id) DO UPDATE SET guild_id = EXCLUDED.guild_id;
        RETURN NULL;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_note_restore_guild() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_note_restore_guild() FROM PUBLIC;
    CREATE TRIGGER web_restore_guilds_note
        AFTER INSERT ON web_restores
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_restore_guild();
"""

SQL = WEB_RESTORE_GUILDS

ROUTING_TABLES: tuple[str, ...] = ("web_restore_guilds",)
