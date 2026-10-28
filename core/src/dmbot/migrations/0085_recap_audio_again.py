"""0085_recap_audio_again

The website's door to new audio (#1418).
"""

from __future__ import annotations

RECAP_AUDIO_AGAIN = """
    -- "Make the audio again" on the website (#1418). The website's role may only READ the audio
    -- columns of a recap (its grant names the columns it may change), so asking for new audio
    -- goes through this door: only a DM of the campaign, only a recap that is ready and still
    -- the DM's to post, and at most once every 10 minutes for a recap (audio_asked_at, the
    -- database's own clock, so a caller cannot lie about the time). It does what the bot does
    -- to queue audio and nothing else: audio queued, tries and wait back to none.
    ALTER TABLE recap_jobs ADD COLUMN audio_asked_at BIGINT NOT NULL DEFAULT 0;
    CREATE FUNCTION dmbot_recap_audio_again(g BIGINT, c TEXT, at_ BIGINT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        -- Only from where the page offers the button: made, or failed. Never while the bot is
        -- making it (its tries and wait would be reset under it) or when it was never wanted.
        IF NOT FOUND OR job.state <> 'ready' OR job.status NOT IN ('draft', 'posting')
           OR job.audio NOT IN ('ready', 'failed') THEN
            RETURN 'not_allowed';
        END IF;
        IF job.audio_asked_at > now_s - 600 THEN
            RETURN 'too_soon';
        END IF;
        UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,
                              audio_error = '', audio_asked_at = now_s
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

SQL = RECAP_AUDIO_AGAIN

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # "Make the audio again" on a DM's recap (#1418): the only write to its audio columns.
    ("dmbot_recap_audio_again", "dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)"),
)
