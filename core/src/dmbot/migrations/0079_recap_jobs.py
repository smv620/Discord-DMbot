"""0079_recap_jobs

The cinematic recap job of a session (#1382).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
    _setting,
)

RECAP_JOBS = (
    _setting("dmbot_recap_mail_lookup", "dmbot.recap_mail_lookup", "TEXT")
    + _setting("dmbot_recap_mail", "dmbot.recap_mail", "TEXT")
    + f"""
    -- The cinematic recap of a session (#1382; docs/PLAN.md, "Cinematic recaps"): a job that runs
    -- after the session, away from the table, and emails the DM when it is ready. The DM reads,
    -- edits and posts it on the website; nothing is ever posted without them. Per campaign,
    -- never shared between campaigns or servers; in its backups; deleted with the campaign. One
    -- row for each session (by when it started); the latest earlier posted one is the bridge.
    --   state      the last stage that finished: queued, beats, checked, prose, housekeeping,
    --              ready, or failed. Each stage can be run again, so a restart picks up where it
    --              stopped (as the extractor does).
    --   attempt    failed checks of the stage now running (0 or 1: the second is the last)
    --   errors     calls that failed (the AI service, the network) in the stage now running
    --   next_at    not before this time (backoff after an error)
    --   leased_to  a worker has it until this time, so two never work on one job
    --   calls      AI calls for the recap now being written, against the per-recap cap
    --   window_count  how many parts the transcript is read in (a long session is read in
    --              overlapping windows), and window_done how many of them are done
    --   spent      AI calls ever made for this session (a Try again does not reset it), for
    --              the campaign's cap for the day
    --   last_error a short fixed reason (a type or a plain phrase), never the recap
    --   beats      the cited beat sheet: what happened, who, and the lines each rests on
    --   summary    the narration with its 《emotion》 and ((performance)) cues
    --   original   the draft as written, so the DM's edits can be counted against it
    --   music      one prompt for each emotion section (slice 2 turns them into audio)
    --   status     draft (the DM has not decided), posting (the DM pressed Post; the bot sends
    --              it), posted, or dropped
    --   tries      how many times the DM asked for another (at most 2)
    --   speakers   whose lines the recap was built from: asked about again at every stage
    --   grade      the grader's numbers (counts only, for quality over time)
    CREATE TABLE recap_jobs (
        {_memory_scope()}
        session_at   BIGINT NOT NULL CHECK (session_at > 0),
        dm_user_id   BIGINT NOT NULL CHECK (dm_user_id > 0),
        state        TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
                         ('queued', 'beats', 'checked', 'prose', 'housekeeping', 'audio',
                          'ready', 'failed')),
        attempt      INTEGER NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 2),
        errors       INTEGER NOT NULL DEFAULT 0 CHECK (errors BETWEEN 0 AND 3),
        restarts     INTEGER NOT NULL DEFAULT 0 CHECK (restarts BETWEEN 0 AND 3),
        next_at      BIGINT NOT NULL DEFAULT 0,
        leased_to    BIGINT NOT NULL DEFAULT 0,
        calls        INTEGER NOT NULL DEFAULT 0 CHECK (calls >= 0),
        spent        INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0),
        window_count INTEGER NOT NULL DEFAULT 0 CHECK (window_count BETWEEN 0 AND 12),
        window_done  INTEGER NOT NULL DEFAULT 0 CHECK (window_done BETWEEN 0 AND 12),
        last_error   TEXT NOT NULL DEFAULT '' CHECK (char_length(last_error) <= 200),
        problems     TEXT[] NOT NULL DEFAULT '{{}}',
        beats        JSONB NOT NULL DEFAULT '[]',
        title        TEXT NOT NULL DEFAULT '' CHECK (char_length(title) <= 120),
        summary      TEXT NOT NULL DEFAULT '' CHECK (char_length(summary) <= 6000),
        original     TEXT NOT NULL DEFAULT '' CHECK (char_length(original) <= 6000),
        housekeeping TEXT[] NOT NULL DEFAULT '{{}}',
        music        TEXT[] NOT NULL DEFAULT '{{}}',
        game_day     INTEGER CHECK (game_day IS NULL OR game_day > 0),
        status       TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'posting', 'posted', 'dropped')),
        tries        INTEGER NOT NULL DEFAULT 0 CHECK (tries BETWEEN 0 AND 2),
        speakers     BIGINT[] NOT NULL DEFAULT '{{}}',
        grade        JSONB NOT NULL DEFAULT '{{}}',
        emailed_at   BIGINT,
        created_at   BIGINT NOT NULL,
        updated_at   BIGINT NOT NULL DEFAULT 0,
        decided_at   BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_at)
    );
    CREATE INDEX recap_jobs_due ON recap_jobs (guild_id, state) WHERE state NOT IN
        ('ready', 'failed');
    """
    + _isolate("recap_jobs")
    + """
    -- The account address a recap's email goes to (the same one the Support replies use: the
    -- payment company's, else the one Discord gave). web_users is forced to a person's own row,
    -- so reading the DM's needs a door like dmbot_user_by_email's: a function with its owner's
    -- rights that answers only while Database.recap_mail() has opened it, and only for a person
    -- who is a DM of a campaign with a recap job in the server it was opened for. A read-only
    -- policy TO the owner role, open only while the function has set its own setting.
    CREATE FUNCTION dmbot_recap_address(who BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found TEXT;
        before TEXT := COALESCE(current_setting('dmbot.recap_mail_lookup', true), '');
    BEGIN
        IF dmbot_recap_mail() IS DISTINCT FROM 'mail' THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs WHERE dm_user_id = who) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_mail_lookup', 'on', true);
        SELECT COALESCE(payment_email, email) INTO found FROM web_users WHERE user_id = who;
        PERFORM set_config('dmbot.recap_mail_lookup', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_mail_lookup ON web_users FOR SELECT TO %I
                USING (dmbot_recap_mail_lookup() = ''on'')',
            current_user);
    END
    $do$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_address(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_address(BIGINT) FROM PUBLIC;
    """
)

SQL = RECAP_JOBS

ISOLATED_TABLES: tuple[str, ...] = ("recap_jobs",)
