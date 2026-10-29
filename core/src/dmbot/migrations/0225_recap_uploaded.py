"""0225_recap_uploaded

A recap written by the table, kept (#2609).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

RECAP_UPLOADED = (
    r"""
    -- A recap written by someone at the table, kept as a session's recap (#2609).
    --   written_by   who wrote it, as the DM typed it; empty for a recap DMbot wrote
    --   uploaded_by  the DM who added it, and uploaded_at when (Unix seconds)
    --   earlier      the recaps it replaced, newest first, as {title, summary, written_by,
    --                posted_url, replaced_at}: never silently overwritten
    --   checked_at   when its names were checked (0: not yet); the bot does that next round
    -- The text columns hold up to 50,000 characters (a length is the writer's choice, #2597).
    -- The constraints are replaced; no row is rewritten, so no row-level security is touched.
    ALTER TABLE recap_jobs
        DROP CONSTRAINT recap_jobs_summary_check,
        DROP CONSTRAINT recap_jobs_original_check,
        ADD CONSTRAINT recap_jobs_summary_check CHECK (char_length(summary) <= 50000),
        ADD CONSTRAINT recap_jobs_original_check CHECK (char_length(original) <= 50000),
        ADD COLUMN written_by  TEXT NOT NULL DEFAULT '' CHECK (char_length(written_by) <= 80),
        ADD COLUMN uploaded_by BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN uploaded_at BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN earlier     JSONB NOT NULL DEFAULT '[]',
        ADD COLUMN checked_at  BIGINT NOT NULL DEFAULT 0;
    -- The first upload of a campaign asks for one confirmation, kept with who and when, as for
    -- other shared content: "written by someone at your table, and you have their OK".
    CREATE TABLE recap_upload_rights (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        user_id      BIGINT NOT NULL CHECK (user_id > 0),
        confirmed_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("recap_upload_rights")
    + r"""
    -- The one door that stores an uploaded recap. For the bot; the website's door passes the
    -- signed-in person. It checks that `who` is a DM of the campaign, that the right to use was
    -- confirmed (now, or before), that nothing is being written for the session, and that an
    -- existing recap is replaced only with the DM's yes, keeping the old one in `earlier`.
    CREATE FUNCTION dmbot_recap_add_uploaded(
        g BIGINT, c TEXT, at_ BIGINT, who BIGINT, ttl TEXT, body TEXT, author TEXT,
        replace_ok BOOLEAN, rights BOOLEAN
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        kept JSONB;
        digest TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g OR who IS NULL OR who <= 0 OR at_ IS NULL
           OR at_ <= 0
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN 'not_allowed';
        END IF;
        IF char_length(btrim(body)) = 0 OR char_length(body) > 50000
           OR char_length(btrim(ttl)) = 0 OR char_length(ttl) > 120
           OR char_length(btrim(author)) = 0 OR char_length(author) > 80 THEN
            RETURN 'bad_text';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_upload_rights r
                       WHERE r.guild_id = g AND r.campaign_id = c) THEN
            IF NOT rights THEN
                RETURN 'needs_rights';
            END IF;
            INSERT INTO recap_upload_rights (guild_id, campaign_id, user_id, confirmed_at)
                VALUES (g, c, who, now_s) ON CONFLICT DO NOTHING;
        END IF;
        digest := encode(sha256(convert_to(ttl || E'\n' || body, 'UTF8')), 'hex');
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state, title,
                                    summary, original, status, created_at, updated_at, emailed_at,
                                    written_by, uploaded_by, uploaded_at, approved_by,
                                    approved_at, approved_hash)
                VALUES (g, c, at_, who, 'ready', ttl, body, body, 'draft', now_s, now_s, now_s,
                        author, who, now_s, who, now_s, digest);
            -- an uploaded recap is approved by being added: it is an accepted version like any
            -- other, so PlotBot's notes and recall see it (#2122, one place for both ways)
            PERFORM dmbot_recap_keep_version(g, c, at_, who, now_s);
            RETURN 'stored';
        END IF;
        -- Going out, or a worker has it right now. A recap that waits is the DM's to replace.
        IF job.status = 'posting' OR job.leased_to > now_s THEN
            RETURN 'busy';
        END IF;
        IF job.state = 'ready' AND job.summary <> '' AND NOT replace_ok THEN
            RETURN 'needs_confirm';
        END IF;
        kept := job.earlier;
        IF job.state = 'ready' AND job.summary <> '' THEN
            kept := (jsonb_build_array(jsonb_build_object(
                'title', job.title, 'summary', job.summary, 'written_by', job.written_by,
                'posted_url', job.posted_url, 'status', job.status, 'replaced_at', now_s))
                || job.earlier);
            -- the ten newest earlier versions
            kept := COALESCE((SELECT jsonb_agg(e) FROM (
                SELECT e FROM jsonb_array_elements(kept) WITH ORDINALITY AS t(e, n)
                ORDER BY n LIMIT 10) q), '[]'::jsonb);
        END IF;
        UPDATE recap_jobs SET
            state = 'ready', attempt = 0, errors = 0, restarts = 0, next_at = 0, leased_to = 0,
            calls = 0, window_count = 0, window_done = 0, last_error = '', problems = '{}',
            beats = '[]', title = ttl, summary = body, original = body, housekeeping = '{}',
            music = '{}', game_day = NULL, status = 'draft', tries = 0, speakers = '{}',
            grade = '{}', emailed_at = now_s, decided_at = NULL, to_confirm = '{}',
            names_state = 0, corrections = '{}', approved_by = who, approved_at = now_s,
            approved_hash = digest, audio_choice = '', split = NULL, critics = NULL,
            audio = 'off', audio_tries = 0, audio_next_at = 0, audio_text_hash = '',
            audio_credits = 0, audio_error = '', audio_asked_at = 0, posting_since = NULL,
            posting_step = NULL, posted_url = NULL, respelled_at = NULL, previous = NULL,
            written_by = author, uploaded_by = who, uploaded_at = now_s, earlier = kept,
            checked_at = 0, dm_user_id = who, updated_at = now_s
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        PERFORM dmbot_recap_keep_version(g, c, at_, who, now_s);
        RETURN 'stored';
    END
    $fn$;
    CREATE FUNCTION dmbot_recap_upload(
        g BIGINT, c TEXT, at_ BIGINT, ttl TEXT, body TEXT, author TEXT, replace_ok BOOLEAN,
        rights BOOLEAN
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        RETURN dmbot_recap_add_uploaded(g, c, at_, dmbot_current_user(), ttl, body, author,
                                        replace_ok, rights);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_add_uploaded(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT,'
            ' TEXT, BOOLEAN, BOOLEAN) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_upload(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN,'
            ' BOOLEAN) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_add_uploaded(
        BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_upload(
        BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN) FROM PUBLIC;
    """
)

SQL = RECAP_UPLOADED

ISOLATED_TABLES: tuple[str, ...] = ("recap_upload_rights",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A recap written by someone at the table, added by a DM on the page (#2609).
    (
        "dmbot_recap_upload",
        "dmbot_recap_upload(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN)",
    ),
)
