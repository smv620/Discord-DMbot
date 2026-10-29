"""0180_shared_recap_session

A shared recap's session (#2418).
"""

from __future__ import annotations

SHARED_RECAP_SESSION = """
    -- A shared recap says which session it was and how long it ran (#2418), the same two facts the
    -- DM's page shows. The public door reads them from the transcripts' own sessions, so the
    -- number is the one the transcripts show. It needs one more read-only policy, tied (like the
    -- others in RECAP_SHARE) to the one recap's server and campaign the function itself found;
    -- only the number and the length leave the function, never a line or a speaker.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_share_read ON transcript_sessions FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())',
            current_user);
    END
    $do$;

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
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
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
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

SQL = SHARED_RECAP_SESSION
