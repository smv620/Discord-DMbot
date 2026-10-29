"""0184_recap_posting_clock

The posting clock (#2435).
"""

from __future__ import annotations

# The recap posting clock (#2435): when a post or a narration began, at which step, and the posted
# message's link, for the website's progress clock. New nullable columns: no row changes. The status
# change itself starts and stops the clock (a trigger), so every writer of `status` (the bot, the
# DM screen's buttons, the website) is covered at one place, and a failed or finished post can never
# leave a clock running. The audio worker sets and clears the 'audio' step (recaps/store.py).
RECAP_POSTING_CLOCK = r"""
    ALTER TABLE recap_jobs ADD COLUMN posting_since BIGINT CHECK (posting_since > 0);
    ALTER TABLE recap_jobs ADD COLUMN posting_step TEXT
        CHECK (posting_step IN ('check', 'post', 'audio'));
    ALTER TABLE recap_jobs ADD COLUMN posted_url TEXT
        CHECK (posted_url ~ '^https://discord\.com/channels/[0-9]+/[0-9]+/[0-9]+$'
               AND char_length(posted_url) <= 200);
    ALTER TABLE recap_jobs ADD CONSTRAINT recap_jobs_posting_pair
        CHECK ((posting_since IS NULL) = (posting_step IS NULL));

    CREATE FUNCTION dmbot_recap_posting_clock() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    BEGIN
        IF NEW.status = 'posting' AND OLD.status IS DISTINCT FROM 'posting' THEN
            NEW.posting_since := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
            NEW.posting_step := 'check';
            NEW.posted_url := NULL;
        ELSIF OLD.status = 'posting' AND NEW.status IS DISTINCT FROM 'posting' THEN
            NEW.posting_since := NULL;  -- posted, put back as a draft or dropped: the clock stops
            NEW.posting_step := NULL;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER recap_jobs_posting_clock BEFORE UPDATE OF status ON recap_jobs
        FOR EACH ROW EXECUTE FUNCTION dmbot_recap_posting_clock();
"""

SQL = RECAP_POSTING_CLOCK
