"""0643_web_adventure_reads

Starting the AI read of a Shared story file from the website (#3694, slice E).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The DM's "Yes, read it" for one file, given on the website and waiting for the bot. The bot does
# what Discord's ReadYesButton does: the plan check (`plan_gate`), then `PlanStore.begin` (a DM, the
# switch on, the confirmation on record, the file readable and not read, the per-file and monthly
# caps) and `start_plan_read`. The read then runs and shows itself where Discord shows it, in
# `adventure_reads` (slice A's list carries its progress), so this row only says whether the read
# STARTED: `done` is "started", and `failed` carries the reason as a code. A row holds no word of
# the book: the file's random id, who asked, the state. One open request a campaign; rows live a
# day, go with their campaign and with the person who asked (0644), and are left out of backups.
SQL = (
    r"""
    CREATE TABLE web_adventure_reads (
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
    CREATE INDEX web_adventure_reads_open ON web_adventure_reads (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_adventure_reads_one_open ON web_adventure_reads (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_adventure_reads")
    + r"""
    -- Queue "Yes, read it" for one file, for a DM of the campaign in a server on the caller's list
    -- (NULL for anyone else). 'gone': no such file here. 'not_readable': the file has no text (it
    -- came back from a backup: give it again). 'already_read': its read is done. 'busy': another
    -- request is open (its id comes back; one file at a time, as Discord's card does). 'too_soon':
    -- ten in an hour. 'ok' with the id otherwise. The bot checks everything again, and the plan and
    -- the caps, which the database does not know.
    CREATE FUNCTION dmbot_web_adventure_read(g BIGINT, c TEXT, doc TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        chunks INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.chunks INTO chunks FROM adventure_documents a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = doc;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        IF chunks = 0 THEN
            RETURN jsonb_build_object('status', 'not_readable');
        END IF;
        IF EXISTS (SELECT 1 FROM adventure_reads r
                   WHERE r.guild_id = g AND r.campaign_id = c AND r.document_id = doc
                     AND r.status = 'done') THEN
            RETURN jsonb_build_object('status', 'already_read');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('adventure_read:' || g::TEXT || c, 0));
        DELETE FROM web_adventure_reads r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        UPDATE web_adventure_reads r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_adventure_reads r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_adventure_reads r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 10 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_adventure_reads (guild_id, campaign_id, id, user_id, document_id,
                                         created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), doc, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Whether the read of a request has started: its state and its error code, for the DM of the
    -- campaign. NULL for anyone else; 'missing' for a row that is gone. Once `done`, the read's own
    -- progress is in the file's list (`dmbot_web_adventure_list`).
    CREATE FUNCTION dmbot_web_adventure_read_state(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_adventure_reads%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_adventure_reads q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = rid;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('id', r.id, 'documentId', r.document_id, 'state', r.state,
            'error', r.error, 'createdAt', r.created_at, 'updatedAt', r.updated_at,
            'serverNow', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_read_state(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_read_state(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("web_adventure_reads",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_adventure_read", "dmbot_web_adventure_read(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_adventure_read_state", "dmbot_web_adventure_read_state(BIGINT, TEXT, TEXT)"),
)
