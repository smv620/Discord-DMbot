"""0639_web_adventure_remove

Removing a file of the Shared story adventure from the website (#3694, slice C).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The website's role has no grant on `adventure_documents`, so a removal is queued and the bot
# does it with the same call as Discord's 🗑 (`AdventureStore.remove`, which checks again that the
# person is a DM of the campaign and takes the file's chunks, plan nodes and links with it). A row
# holds no word of the book: the file's id (a random key), who asked and how it went. One open
# removal of a file at a time; rows live a day, go with their campaign and with the person who
# asked (0640), and are left out of backups.
SQL = (
    r"""
    CREATE TABLE web_adventure_removals (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        document_id TEXT NOT NULL CHECK (document_id ~ '^[0-9a-f]{32}$'),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state = 'failed') = (error <> ''))
    );
    CREATE INDEX web_adventure_removals_open ON web_adventure_removals (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_adventure_removals_one_open
        ON web_adventure_removals (guild_id, campaign_id, document_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_adventure_removals")
    + r"""
    -- Queue the removal of one file, for a DM of the campaign in a server on the caller's list
    -- (NULL for anyone else, a player included). 'gone': no such file in this campaign. The same
    -- request again while one is open answers with the open one's id. 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_web_adventure_remove(g BIGINT, c TEXT, doc TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM adventure_documents a
                       WHERE a.guild_id = g AND a.campaign_id = c AND a.id = doc) THEN
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('adventure_remove:' || g::TEXT || c, 0));
        DELETE FROM web_adventure_removals r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- A claim the bot took and went quiet on for ten minutes goes back to waiting (removing a
        -- file twice does no harm: the second finds it gone).
        UPDATE web_adventure_removals r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_adventure_removals r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.document_id = doc
              AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        INSERT INTO web_adventure_removals (guild_id, campaign_id, id, user_id, document_id,
                                            created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), doc, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- How one removal went, for the DM who asked: its state and its error word. NULL for anyone
    -- else, and 'missing' for a row that is gone (a day old, or never there).
    CREATE FUNCTION dmbot_web_adventure_remove_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_adventure_removals%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_adventure_removals q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = rid;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('id', r.id, 'state', r.state, 'error', r.error,
                                  'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_remove(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_remove_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_remove(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_remove_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("web_adventure_removals",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_adventure_remove", "dmbot_web_adventure_remove(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_adventure_remove_read", "dmbot_web_adventure_remove_read(BIGINT, TEXT, TEXT)"),
)
