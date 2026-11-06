"""0258_web_backup_parts

A website copy carries its transcripts (#2772).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_BACKUP_PARTS = (
    """
    -- The transcripts files of a copy the website downloads (#2772, #2552): Discord's backup
    -- sends a campaign's transcripts as extra files next to the main one, and a website copy is
    -- the same set. One row is one such file, waiting in the database for the person who asked
    -- (like `web_backups.file`: kept 15 minutes at most, handed over once, never logged, the DM's
    -- secrets inside no more than in any backup). `web_backups.parts` says how many there were.
    -- The row goes with its copy; `data` is emptied as the file is fetched.
    ALTER TABLE web_backups ADD COLUMN parts INT NOT NULL DEFAULT 0;
    CREATE TABLE web_backup_parts (
        guild_id  BIGINT NOT NULL,
        backup_id TEXT NOT NULL,
        FOREIGN KEY (guild_id, backup_id)
            REFERENCES web_backups (guild_id, id) ON DELETE CASCADE ON UPDATE CASCADE,
        n         INT NOT NULL CHECK (n >= 1 AND n <= 24),
        data      BYTEA,
        size      INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, backup_id, n)
    );
    """
    + _isolate("web_backup_parts")
    + r"""
    -- (Asking for a copy is 0219's door, unchanged: a copy whose main file has been taken is never
    -- handed back, so a retry after a failed part starts a fresh copy and the old one's leftovers
    -- go with the 15-minute sweep, #2778.)

    -- Where one copy stands: the same answer as before, plus the files still waiting for the
    -- person (number and size, never the bytes).
    CREATE OR REPLACE FUNCTION dmbot_web_backup_read(g BIGINT, c TEXT, bid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
        waiting JSONB;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT b.state, b.stage, b.error, b.file_name, b.size, b.parts, b.created_at,
               b.updated_at, (b.file IS NOT NULL) AS has_file INTO r
            FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid
              AND b.user_id = dmbot_current_user()
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('n', p.n, 'size', p.size) ORDER BY p.n),
                        '[]'::jsonb) INTO waiting
            FROM web_backup_parts p
            WHERE p.guild_id = g AND p.backup_id = bid AND p.data IS NOT NULL;
        -- Gone once the main file and every part have been fetched.
        IF r.state = 'done' AND NOT r.has_file AND waiting = '[]'::jsonb THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'fileName', r.file_name, 'size', r.size, 'fileWaiting', r.has_file,
            'parts', waiting, 'partsTotal', r.parts,
            'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;

    -- One transcripts file, once, for the person who asked: emptied from the row as it is read.
    -- NULL when it is not theirs, not ready or already taken.
    CREATE FUNCTION dmbot_web_backup_take_part(g BIGINT, c TEXT, bid TEXT, part INT)
    RETURNS BYTEA
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out BYTEA;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT p.data INTO out
            FROM web_backup_parts p JOIN web_backups b
                ON b.guild_id = p.guild_id AND b.id = p.backup_id
            WHERE p.guild_id = g AND p.backup_id = bid AND p.n = part AND p.data IS NOT NULL
              AND b.campaign_id = c AND b.user_id = dmbot_current_user() AND b.state = 'done'
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900
            FOR UPDATE OF p;
        IF out IS NULL THEN
            RETURN NULL;
        END IF;
        UPDATE web_backup_parts p SET data = NULL, size = 0
            WHERE p.guild_id = g AND p.backup_id = bid AND p.n = part;
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_take_part(BIGINT, TEXT, TEXT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_backup_take_part(BIGINT, TEXT, TEXT, INT) FROM PUBLIC;
    """
)

SQL = WEB_BACKUP_PARTS

ISOLATED_TABLES: tuple[str, ...] = ("web_backup_parts",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_backup_take_part", "dmbot_web_backup_take_part(BIGINT, TEXT, TEXT, INT)"),
)
