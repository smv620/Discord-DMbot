"""0207_recap_approve_unchecked

Approve an unchecked draft (#2305).
"""

from __future__ import annotations

RECAP_APPROVE_UNCHECKED = """
    -- "Approve it as it is" for a recap whose critics could not be reached for 3 days (#2305).
    -- The website's role may not write recap_jobs, so this is the door: only a DM of the campaign,
    -- only a draft still in the critics' loop that has waited at least `wait_s` seconds for its
    -- check (the caller passes the 3 days, so the number lives in one place), and only the text
    -- the DM read: `expected` is the hash of the title and summary the page showed, and a draft
    -- the worker has revised since approves nothing ('changed'). A second press after an approval
    -- answers 'approved' again and changes nothing. It marks the loop done, records who and when
    -- in the loop's JSON, adds the note that the draft was never fully checked, and lets the
    -- worker run on to the title step (next_at = 0), which makes it ready.
    -- A new function: no rows change, so no row-level-security policy is needed.
    CREATE FUNCTION dmbot_recap_approve_unchecked(
        g BIGINT, c TEXT, at_ BIGINT, wait_s BIGINT, note TEXT, expected TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        since_s BIGINT;
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
        IF NOT FOUND OR job.status <> 'draft' OR job.state <> 'prose'
           OR job.critics IS NULL OR job.summary = '' THEN
            RETURN 'not_allowed';
        END IF;
        IF COALESCE((job.critics->>'approved_at')::BIGINT, 0) > 0 THEN
            RETURN 'approved';  -- already approved: a second press changes nothing
        END IF;
        IF COALESCE(job.critics->>'done', 'false') = 'true' THEN
            RETURN 'not_allowed';
        END IF;
        IF expected <> encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')),
                              'hex') THEN
            RETURN 'changed';
        END IF;
        -- Waiting for the check at all (a wait was counted), and for long enough.
        since_s := COALESCE((job.critics->>'since')::BIGINT, 0);
        IF COALESCE((job.critics->>'waits')::BIGINT, 0) = 0 OR since_s = 0
           OR now_s - since_s < wait_s THEN
            RETURN 'not_yet';
        END IF;
        UPDATE recap_jobs SET
            critics = job.critics || jsonb_build_object(
                'done', true, 'approved_by', dmbot_current_user(), 'approved_at', now_s,
                'notes', CASE WHEN jsonb_typeof(job.critics->'notes') = 'array'
                              THEN job.critics->'notes' ELSE '[]'::jsonb END || to_jsonb(note)),
            last_error = '', errors = 0, next_at = 0
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'approved';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve_unchecked(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_approve_unchecked(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT)
        FROM PUBLIC;
    """

SQL = RECAP_APPROVE_UNCHECKED

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # ... and approving a draft that waited 3 days for its check, as it is (#2305).
    (
        "dmbot_recap_approve_unchecked",
        "dmbot_recap_approve_unchecked(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT)",
    ),
)
