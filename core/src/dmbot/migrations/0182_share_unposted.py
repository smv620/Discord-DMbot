"""0182_share_unposted

A link for an approved, unposted recap (#2419).
"""

from __future__ import annotations

SHARE_UNPOSTED = """
    -- A recap's link for one the DM approved and has not posted (#2419: "Approve but don't post",
    -- then share it their own way). A link is still only made when the DM asks for it (the
    -- door's "on" and "new"), never by approving. It works while the recap is being posted or
    -- posted (the DM asked for the post, and the link would otherwise go dead for the minute it
    -- takes), or while it is a draft whose text is still exactly the text the DM approved: the
    -- public read checks that itself, so an edit after sharing hides the recap until it is
    -- approved again, and an unapproved draft never answers. Both doors are otherwise as before.
    CREATE OR REPLACE FUNCTION dmbot_recap_share(g BIGINT, c TEXT, at_ BIGINT, act TEXT, tok TEXT)
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
        IF NOT (
           job.status IN ('posting', 'posted')
           OR (job.status = 'draft' AND job.state = 'ready' AND job.approved_hash <> ''
               AND job.approved_hash = encode(sha256(convert_to(
                   job.title || E'\n' || job.summary, 'UTF8')), 'hex'))) THEN
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

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        sess_number BIGINT;
        sess_seconds BIGINT;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND (
            status IN ('posting', 'posted')
            OR (status = 'draft' AND state = 'ready' AND approved_hash <> ''
                AND approved_hash = encode(sha256(convert_to(
                    title || E'\n' || summary, 'UTF8')), 'hex')));
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        -- Which session it was and how long it ran, as the transcripts page numbers and times it
        -- (`dmbot_web_transcript_sessions`: Session N in order of start among the campaign's saved
        -- sessions, those whose transcripts were deleted by retention included). Only these two
        -- numbers leave: nothing of the transcript itself.
        SELECT x.number, x.seconds INTO sess_number, sess_seconds FROM (
            SELECT t.started_at, row_number() OVER (ORDER BY t.started_at, t.id) AS number,
                   CASE WHEN t.ended_at IS NULL THEN NULL
                        ELSE GREATEST(t.ended_at - t.started_at, 0) END AS seconds
            FROM transcript_sessions t
            WHERE t.guild_id = job.guild_id AND t.campaign_id = job.campaign_id
              AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) x
        WHERE x.started_at = job.session_at ORDER BY x.number LIMIT 1;
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'sessionNumber', sess_number, 'sessionSeconds', sess_seconds,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

SQL = SHARE_UNPOSTED
