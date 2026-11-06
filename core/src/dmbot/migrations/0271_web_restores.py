"""0271_web_restores

A backup loaded from the website (#2745).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_RESTORES = (
    """
    -- A backup loaded from the website (#2745), waiting for the bot. The website's role cannot
    -- write the campaign tables, so it stages the file here through the doors below, and the bot
    -- applies it with the same code as Discord's `/dmbot restore`: after checking again the plan,
    -- the free campaign slot, that the person is still in the server and (to replace a campaign)
    -- still one of its DMs, and that the campaign is not playing. `main` is the copy and
    -- `web_restore_parts` its transcripts files (all of them or none, as in Discord). One row is
    -- one restore: `receiving` while the person's own files are still arriving, then `waiting`,
    -- `working` (with `stage`, `done_parts` of `parts_wanted` and `updated_at` for the shared
    -- progress shape of #2564), then `done` or `failed` with a plain reason word. The bytes are
    -- emptied when the bot has applied or refused it, and the row goes 24 hours after it began,
    -- whichever is first (the bot sweeps; the doors also do). A copy holds the campaign's secrets,
    -- like any backup, so it is never logged. Isolated per server, read by the person who made it
    -- only, one pending restore per person per server, and not itself in a backup.
    CREATE TABLE web_restores (
        guild_id      BIGINT NOT NULL,
        id            TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id       BIGINT NOT NULL CHECK (user_id > 0),
        -- The campaign to replace, or NULL for a new one. Not a foreign key: the bot judges whether
        -- it still exists and is the person's, and a row must outlive a deleted campaign to say so.
        replace_id    TEXT,
        name          TEXT NOT NULL CHECK (char_length(name) <= 100),
        parts_wanted  INT NOT NULL CHECK (parts_wanted BETWEEN 0 AND 24),
        main          BYTEA,
        state         TEXT NOT NULL DEFAULT 'receiving'
            CHECK (state IN ('receiving', 'waiting', 'working', 'done', 'failed')),
        stage         TEXT NOT NULL DEFAULT 'restore_receiving'
            CHECK (stage IN ('restore_receiving', 'restore_waiting', 'restore_reading',
                             'restore_writing', 'done', 'failed')),
        error         TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        done_parts    INT NOT NULL DEFAULT 0,
        left_out      INT NOT NULL DEFAULT 0,
        without_parts BOOLEAN NOT NULL DEFAULT FALSE,
        result_id     TEXT,
        created_at    BIGINT NOT NULL,
        claimed_at    BIGINT NOT NULL DEFAULT 0,
        updated_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_restores_open ON web_restores (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    -- One restore on its way per person across every server (a copy can be 625 MB): the index sees
    -- all servers' rows even though a door sees one server's.
    CREATE UNIQUE INDEX web_restores_one_each ON web_restores (user_id)
        WHERE state IN ('receiving', 'waiting', 'working');
    CREATE TABLE web_restore_parts (
        guild_id   BIGINT NOT NULL,
        restore_id TEXT NOT NULL,
        n          INT NOT NULL CHECK (n BETWEEN 1 AND 24),
        data       BYTEA,
        -- ON UPDATE CASCADE: a foreign key that carries guild_id (CLAUDE.md, #2550).
        FOREIGN KEY (guild_id, restore_id)
            REFERENCES web_restores (guild_id, id) ON DELETE CASCADE ON UPDATE CASCADE,
        PRIMARY KEY (guild_id, restore_id, n)
    );
    """
    + _isolate("web_restores")
    + _isolate("web_restore_parts")
    + r"""
    -- The bytes never outlive the end of a restore, whatever the bot does: the moment a row turns
    -- done or failed, the copy is emptied and its transcripts files go (the 24-hour sweep catches
    -- the rows that never ended).
    CREATE FUNCTION web_restores_empty() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        IF NEW.state IN ('done', 'failed') THEN
            NEW.main := NULL;
            DELETE FROM web_restore_parts p
                WHERE p.guild_id = NEW.guild_id AND p.restore_id = NEW.id;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER web_restores_empty BEFORE UPDATE ON web_restores
        FOR EACH ROW EXECUTE FUNCTION web_restores_empty();

    -- Stage a restore: the copy itself, already checked by the API (a backup of this name, asking
    -- for this many transcripts files). `replace_` is a campaign of the caller's to swap for the
    -- copy, or NULL for a new campaign. The door checks what it can see: the caller is in the
    -- server (and, to replace, a DM of that campaign), no other restore of theirs is still pending
    -- in any server, and fewer than 5 in the last hour here. Answers 'ok' with the id and the state
    -- (`receiving`), 'busy' with the id of the one pending, 'too_soon', or 'bad_target'; NULL for
    -- anyone not in the server.
    CREATE FUNCTION dmbot_web_restore_add(
        g BIGINT, replace_ TEXT, nm TEXT, wanted INT, copy BYTEA
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL OR g IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF copy IS NULL OR nm IS NULL OR wanted IS NULL OR wanted < 0 OR wanted > 24 THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        IF replace_ IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM campaign_dms d
               WHERE d.guild_id = g AND d.campaign_id = replace_ AND d.user_id = me) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('web_restore:' || me::TEXT, 0));
        DELETE FROM web_restores r WHERE r.guild_id = g AND r.created_at < now_s - 86400;
        SELECT r.id INTO have FROM web_restores r
            WHERE r.guild_id = g AND r.user_id = me
              AND r.state IN ('receiving', 'waiting', 'working')
            ORDER BY r.created_at DESC LIMIT 1;
        IF FOUND THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        -- (Counted in this server only; the API's per-person rate limit is the real one.)
        IF (SELECT count(*) FROM web_restores r
            WHERE r.guild_id = g AND r.user_id = me AND r.created_at > now_s - 3600) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        -- Always `receiving` first: nothing goes to the bot until the person has seen what it
        -- will do and pressed Load it (`dmbot_web_restore_go`), even with no transcripts files.
        BEGIN
            INSERT INTO web_restores (guild_id, id, user_id, replace_id, name, parts_wanted, main,
                                      created_at, updated_at)
                VALUES (g, rid, me, replace_, left(nm, 100), wanted, copy, now_s, now_s);
        EXCEPTION WHEN unique_violation THEN
            -- Pending in another server (this server's was found above): no id to give.
            RETURN jsonb_build_object('status', 'busy');
        END;
        RETURN jsonb_build_object('status', 'ok', 'id', rid, 'state', 'receiving');
    END
    $fn$;

    -- One transcripts file of a restore that is still receiving, numbered 1 to the count the copy
    -- asked for; sending a number again replaces it. 'ok' with how many have arrived, 'bad_part'
    -- for a number outside the count, 'not_receiving' once it is on its way; NULL when it is not
    -- the caller's.
    CREATE FUNCTION dmbot_web_restore_part(g BIGINT, rid TEXT, num INT, blob BYTEA) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_restores%ROWTYPE;
        got INT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.state <> 'receiving' THEN
            RETURN jsonb_build_object('status', 'not_receiving');
        END IF;
        IF num IS NULL OR num < 1 OR num > r.parts_wanted OR blob IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_part');
        END IF;
        INSERT INTO web_restore_parts (guild_id, restore_id, n, data) VALUES (g, rid, num, blob)
            ON CONFLICT (guild_id, restore_id, n) DO UPDATE SET data = EXCLUDED.data;
        SELECT count(*) INTO got FROM web_restore_parts p
            WHERE p.guild_id = g AND p.restore_id = rid;
        RETURN jsonb_build_object('status', 'ok', 'received', got);
    END
    $fn$;

    -- Send it to the bot: with every transcripts file in (`without` false), or restoring without
    -- them (`without` true, as Discord allows: all of them or none). 'ok', 'missing' when files
    -- are still to come, 'not_receiving' if it already went; NULL when it is not the caller's.
    CREATE FUNCTION dmbot_web_restore_go(g BIGINT, rid TEXT, without BOOLEAN) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_restores%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        got INT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.state <> 'receiving' THEN
            RETURN jsonb_build_object('status', 'not_receiving');
        END IF;
        SELECT count(*) INTO got FROM web_restore_parts p
            WHERE p.guild_id = g AND p.restore_id = rid;
        IF COALESCE(without, false) THEN
            DELETE FROM web_restore_parts p WHERE p.guild_id = g AND p.restore_id = rid;
        ELSIF got <> r.parts_wanted THEN
            RETURN jsonb_build_object('status', 'missing', 'received', got);
        END IF;
        UPDATE web_restores x SET state = 'waiting', stage = 'restore_waiting',
               without_parts = COALESCE(without, false), updated_at = now_s
            WHERE x.guild_id = g AND x.id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Where a restore stands, for the person who made it (nobody else): the progress fields, the
    -- name of the copy (the campaign's own name, which they supplied), how many transcripts files
    -- it asks for and has, and, once it has ended, the plain reason word and how many lines were
    -- left out for people who had deleted theirs. NULL when it is not theirs.
    CREATE FUNCTION dmbot_web_restore_read(g BIGINT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
        got INT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        -- Named columns, so a poll never reads the copy itself.
        SELECT x.state, x.stage, x.error, x.name, x.parts_wanted, x.done_parts, x.left_out,
               x.replace_id, x.result_id, x.without_parts, x.created_at, x.updated_at INTO r
            FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
              AND x.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        SELECT count(*) INTO got FROM web_restore_parts p
            WHERE p.guild_id = g AND p.restore_id = rid;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'name', r.name, 'partsWanted', r.parts_wanted, 'partsReceived', got,
            'doneParts', r.done_parts, 'leftOut', r.left_out, 'replaceId', r.replace_id,
            'resultId', r.result_id, 'withoutParts', r.without_parts,
            'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;

    -- The caller's own pending restore in this server, if any (id only), so a page that was
    -- closed can find its way back.
    CREATE FUNCTION dmbot_web_restore_pending(g BIGINT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out TEXT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT x.id INTO out FROM web_restores x
            WHERE x.guild_id = g AND x.user_id = dmbot_current_user()
              AND x.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400
            ORDER BY x.created_at DESC LIMIT 1;
        RETURN out;
    END
    $fn$;

    -- Take it back: the person's own restore, while the bot has not started it. The bytes go with
    -- the row. True when something was removed.
    CREATE FUNCTION dmbot_web_restore_cancel(g BIGINT, rid TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        st TEXT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT x.state INTO st FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'gone';
        END IF;
        -- The bot has it: it cannot be taken back, and the page must go on showing it.
        IF st = 'working' THEN
            RETURN 'working';
        END IF;
        DELETE FROM web_restores x WHERE x.guild_id = g AND x.id = rid;
        RETURN 'ok';
    END
    $fn$;
    -- Who owns the campaign a restore would replace, for the plan check made before any bytes are
    -- stored: the owner's id as text (or '' when it has none), only for a DM of that campaign in
    -- this server. NULL for anyone else.
    CREATE FUNCTION dmbot_web_restore_target(g BIGINT, c TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        own BIGINT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT k.owner_user_id INTO own FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        RETURN COALESCE(own::TEXT, '');
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_target(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_add(BIGINT, TEXT, TEXT, INT, BYTEA)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_part(BIGINT, TEXT, INT, BYTEA)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_go(BIGINT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_pending(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_cancel(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_restore_target(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_add(BIGINT, TEXT, TEXT, INT, BYTEA) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_part(BIGINT, TEXT, INT, BYTEA) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_go(BIGINT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_read(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_pending(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_cancel(BIGINT, TEXT) FROM PUBLIC;
"""
)

SQL = WEB_RESTORES

ISOLATED_TABLES: tuple[str, ...] = (
    "web_restores",
    "web_restore_parts",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_restore_target", "dmbot_web_restore_target(BIGINT, TEXT)"),
    ("dmbot_web_restore_add", "dmbot_web_restore_add(BIGINT, TEXT, TEXT, INT, BYTEA)"),
    ("dmbot_web_restore_part", "dmbot_web_restore_part(BIGINT, TEXT, INT, BYTEA)"),
    ("dmbot_web_restore_go", "dmbot_web_restore_go(BIGINT, TEXT, BOOLEAN)"),
    ("dmbot_web_restore_read", "dmbot_web_restore_read(BIGINT, TEXT)"),
    ("dmbot_web_restore_pending", "dmbot_web_restore_pending(BIGINT)"),
    ("dmbot_web_restore_cancel", "dmbot_web_restore_cancel(BIGINT, TEXT)"),
)
