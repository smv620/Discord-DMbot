"""0140_recap_voice

... and a short clip it may speak instead (#2113).
"""

from __future__ import annotations

RECAP_VOICE = r"""
    -- Speaking instead of typing on the recap page (#2113). The clip waits in the request row
    -- until the bot has written it down; the same statement that stores the text sets the clip
    -- to NULL, and a sweep clears any clip older than ten minutes whatever happened. It is never
    -- written to disk, never logged, and the table is left out of backups. The words are the
    -- DM's own and live in the row for an hour at most.
    ALTER TABLE recap_requests DROP CONSTRAINT recap_requests_kind_check;
    ALTER TABLE recap_requests ADD CONSTRAINT recap_requests_kind_check
        CHECK (kind IN ('instructions', 'voice'));
    ALTER TABLE recap_requests DROP CONSTRAINT recap_requests_body_check;
    ALTER TABLE recap_requests ADD CONSTRAINT recap_requests_body_check CHECK (
        (kind = 'instructions' AND char_length(body) BETWEEN 1 AND 1000)
        OR (kind = 'voice' AND body = ''));
    ALTER TABLE recap_requests ADD COLUMN clip BYTEA
        CHECK (clip IS NULL OR octet_length(clip) BETWEEN 1 AND 2097152);
    ALTER TABLE recap_requests ADD COLUMN clip_type TEXT NOT NULL DEFAULT ''
        CHECK (clip_type IN ('', 'audio/webm', 'audio/ogg'));

    CREATE FUNCTION dmbot_recap_voice_add(
        g BIGINT, c TEXT, at_ BIGINT, data BYTEA, ctype TEXT, terms INT, outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- Best effort (#2163: nothing writes installs.left_at yet): a server the bot has left
        -- takes no new clips. The promise that no clip outlives ten minutes does not rest on this:
        -- the all-server sweep (dmbot_sweep_old_clips) keeps it.
        IF EXISTS (SELECT 1 FROM installs i WHERE i.guild_id = g AND i.left_at IS NOT NULL) THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        -- Audio DMbot holds, even for minutes, needs the speaker's agreement to be recorded
        -- under the current wording, naming the outside company in use if there is one
        -- (`terms` and `outside` are the code's TERMS_VERSION and TRANSCRIBER). The bot checks
        -- again.
        IF NOT EXISTS (SELECT 1 FROM consent k
                       WHERE k.guild_id = g AND k.user_id = dmbot_current_user()
                         AND k.terms_version = terms
                         AND (outside IS NULL OR k.outside_to = outside)) THEN
            RETURN jsonb_build_object('status', 'no_consent');
        END IF;
        IF ctype IS NULL OR ctype NOT IN ('audio/webm', 'audio/ogg') THEN
            RETURN jsonb_build_object('status', 'bad_type');
        END IF;
        IF data IS NULL OR octet_length(data) = 0 THEN
            RETURN jsonb_build_object('status', 'bad_clip');
        END IF;
        IF octet_length(data) > 2097152 THEN
            RETURN jsonb_build_object('status', 'too_big');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs j
                       WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                         AND j.state = 'ready' AND j.status = 'draft') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_request:' || g::TEXT || c, 0));
        -- A clip older than ten minutes and any row older than an hour go here, for this
        -- campaign, whenever a new clip comes (the bot's own sweep covers the servers it is in).
        UPDATE recap_requests r SET clip = NULL
            WHERE r.guild_id = g AND r.campaign_id = c AND r.clip IS NOT NULL
              AND r.created_at < now_s - 600;
        DELETE FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 3600;
        IF (SELECT count(*) FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'voice'
              AND r.state IN ('waiting', 'working', 'done')  -- not a failed one
              AND r.created_at > now_s - 600) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,
                                    clip, clip_type, created_at)
            VALUES (g, c, rid, at_, dmbot_current_user(), 'voice', '', data, ctype, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Emptying old clips in every server, the ones the bot has left included, by one function
    -- and nothing else: it sets the cleanup marker for its own statement only and does one
    -- thing, empty the clip of a request older than ten minutes. It is the bot's: not granted
    -- to the website's role (the policies below mean nothing without the marker, and the
    -- website's role has no right to the table anyway).
    -- (An UPDATE's new row must pass the SELECT rule too, so that rule cannot ask for the clip.)
    CREATE POLICY old_clips_seen ON recap_requests FOR SELECT
        USING (dmbot_cleanup() = 'old-clips' AND created_at < dmbot_now() - 600);
    CREATE POLICY old_clips_emptied ON recap_requests FOR UPDATE
        USING (dmbot_cleanup() = 'old-clips' AND clip IS NOT NULL
               AND created_at < dmbot_now() - 600)
        WITH CHECK (dmbot_cleanup() = 'old-clips' AND clip IS NULL);
    CREATE FUNCTION dmbot_sweep_old_clips() RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        emptied BIGINT;
    BEGIN
        PERFORM set_config('dmbot.cleanup', 'old-clips', true);
        UPDATE recap_requests SET clip = NULL
            WHERE clip IS NOT NULL AND created_at < dmbot_now() - 600;
        GET DIAGNOSTICS emptied = ROW_COUNT;
        PERFORM set_config('dmbot.cleanup', '', true);
        RETURN emptied;
    END
    $fn$;

    -- A clip's state and the words written from it, to the DM who sent it and no one else.
    CREATE FUNCTION dmbot_recap_voice_read(g BIGINT, c TEXT, at_ BIGINT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r recap_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.kind = 'voice' AND x.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'answer', r.answer);
    END
    $fn$;
    -- The typed notes' limit counts typed notes only: clips have their own (above).
    CREATE OR REPLACE FUNCTION dmbot_recap_request_add(g BIGINT, c TEXT, at_ BIGINT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF txt IS NULL OR char_length(btrim(txt)) = 0 OR char_length(txt) > 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs j
                       WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                         AND j.state = 'ready' AND j.status = 'draft') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_request:' || g::TEXT || c, 0));
        -- The one-hour promise holds for a server the bot has left too: old words go here.
        DELETE FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 3600;
        IF (SELECT count(*) FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'instructions'
              AND r.state IN ('waiting', 'working')
              AND r.created_at > now_s - 600) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,
                                    created_at)
            VALUES (g, c, rid, at_, dmbot_current_user(), 'instructions', btrim(txt), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());  -- (reset by REPLACE)
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_voice_add(BIGINT, TEXT, BIGINT, BYTEA, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_sweep_old_clips()'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_voice_add(
        BIGINT, TEXT, BIGINT, BYTEA, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_sweep_old_clips() FROM PUBLIC;
    """

SQL = RECAP_VOICE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A spoken note: the clip is held until the bot has written it down (#2113).
    (
        "dmbot_recap_voice_add",
        "dmbot_recap_voice_add(BIGINT, TEXT, BIGINT, BYTEA, TEXT, INT, TEXT)",
    ),
    ("dmbot_recap_voice_read", "dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT)"),
)
