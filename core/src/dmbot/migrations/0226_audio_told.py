"""0226_audio_told

The DM is told once when the audio is done (#2621).
"""

from __future__ import annotations

RECAP_AUDIO_TOLD = """
    -- The DM is told once, on the DM screen, when a recap's audio is ready or has failed (#2621).
    -- `audio_told` holds the audio state that was told ('ready' or 'failed'); '' when none is. A
    -- trigger clears it each time the audio is queued, whichever door queued it (approval, the
    -- website's Make it again, the bot), so a new making is told again and a restart never repeats
    -- a notice. Recaps whose audio is already ready or failed are marked as told: no old news.
    ALTER TABLE recap_jobs ADD COLUMN audio_told TEXT NOT NULL DEFAULT '';
    CREATE POLICY migrate_backfill ON recap_jobs FOR ALL USING (true) WITH CHECK (true);
    UPDATE recap_jobs SET audio_told = audio WHERE audio IN ('ready', 'failed');
    DROP POLICY migrate_backfill ON recap_jobs;
    CREATE FUNCTION dmbot_recap_audio_told_clock() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    BEGIN
        IF NEW.audio = 'queued' AND OLD.audio IS DISTINCT FROM 'queued' THEN
            NEW.audio_told := '';
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER recap_jobs_audio_told BEFORE UPDATE OF audio ON recap_jobs
        FOR EACH ROW EXECUTE FUNCTION dmbot_recap_audio_told_clock();
"""

SQL = RECAP_AUDIO_TOLD
