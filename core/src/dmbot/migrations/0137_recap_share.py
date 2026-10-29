"""0137_recap_share

A public link for a posted recap (#2118).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _setting,
)

RECAP_SHARE = (
    _setting("dmbot_recap_share_tok", "dmbot.recap_share_tok", "TEXT")
    + _setting("dmbot_recap_share_guild", "dmbot.recap_share_guild", "BIGINT")
    + _setting("dmbot_recap_share_campaign", "dmbot.recap_share_campaign", "TEXT")
    + """
    -- A public, read-only link for a posted recap (#2118). The token is random (256 bits,
    -- URL-safe, made by the API), stored with the recap, and is the whole key: a link finds one
    -- recap and nothing else about its campaign or server. A defaulted-NULL column changes no rows.
    ALTER TABLE recap_jobs ADD COLUMN share_token TEXT UNIQUE
        CHECK (share_token IS NULL OR share_token ~ '^[A-Za-z0-9_-]{43}$');

    -- The public read has no signed-in person and no server set, so the function that serves it
    -- needs a way past the row-level rules on the three tables it reads (the recap, and the names
    -- for the secret check). Each is a read-only policy TO the role that owns the function (the
    -- one running this migration) and tied to what the function itself sets: the recap to the
    -- token it was given, the names to the one recap's server and campaign it then found. Never the
    -- website's role: even if it set these settings itself, the policy is not for it, and a
    -- function that runs as the owner sees only that one recap and its campaign's names.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_share_read ON recap_jobs FOR SELECT TO %I
                USING (share_token IS NOT NULL AND share_token = dmbot_recap_share_tok())',
            current_user);
        EXECUTE format(
            'CREATE POLICY recap_share_read ON memory_aliases FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())', current_user);
        EXECUTE format(
            'CREATE POLICY recap_share_read ON campaigns FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND id = dmbot_recap_share_campaign())', current_user);
        EXECUTE format(
            'CREATE POLICY recap_share_read ON memory_entities FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())', current_user);
    END
    $do$;

    -- dmbot_recap_shared: the posted recap a token names, or NULL for anything else (no such token,
    -- turned off, not posted): one answer. Returns the table version's words, when it was posted,
    -- where its audio is (for the API only, never sent on), and the campaign's secret and ordinary
    -- names so the API can check the text again on every read.
    CREATE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;

    -- dmbot_recap_share: a DM of the campaign turns a posted recap's link on, off, or makes a new
    -- one. `tok` is a fresh token from the API, used by "on" (when there is none) and "new".
    -- Returns {"status": "ok" | "not_posted", "token": the link's token or null}; NULL unless a DM.
    CREATE FUNCTION dmbot_recap_share(g BIGINT, c TEXT, at_ BIGINT, act TEXT, tok TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF act = 'off' THEN
            UPDATE recap_jobs SET share_token = NULL
                WHERE guild_id = g AND campaign_id = c AND session_at = at_;
            RETURN jsonb_build_object('status', 'ok', 'token', NULL);
        END IF;
        IF job.status <> 'posted' THEN
            RETURN jsonb_build_object('status', 'not_posted', 'token', NULL);
        END IF;
        IF (act = 'new' OR job.share_token IS NULL)
           AND (tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$') THEN
            RETURN NULL;  -- the API always passes a good one
        END IF;
        IF act = 'new' OR job.share_token IS NULL THEN
            UPDATE recap_jobs SET share_token = tok
                WHERE guild_id = g AND campaign_id = c AND session_at = at_;
            RETURN jsonb_build_object('status', 'ok', 'token', tok);
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'token', job.share_token);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_shared(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

SQL = RECAP_SHARE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A posted recap's public link (#2118): turned on and off by a DM, read by anyone with it.
    ("dmbot_recap_share", "dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    ("dmbot_recap_shared", "dmbot_recap_shared(TEXT)"),
)
