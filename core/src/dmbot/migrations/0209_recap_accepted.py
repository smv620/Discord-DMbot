"""0209_recap_accepted

Each approved version of a recap (#2122).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _approve_with_versions,
    _isolate,
)

RECAP_ACCEPTED = (
    """
    -- Each version of a recap text the DM approved (#2122, docs/PLAN.md, "PlotBot checks every
    -- recap"): it becomes that session's summary in the campaign notes, which PlotBot, the
    -- pre-session note, recall and later checks read (the newest version per session). Never
    -- overwritten: an approval of different text adds the next version. A new table, so no rows
    -- change and no backfill policy; rows go with their campaign.
    CREATE TABLE recap_accepted (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        version     INTEGER NOT NULL CHECK (version >= 1),
        text_hash   TEXT NOT NULL,
        title       TEXT NOT NULL,
        summary     TEXT NOT NULL,
        accepted_by BIGINT NOT NULL,
        accepted_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_at, version),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("recap_accepted")
    + """
    -- Keeps the job's current text as the session's next accepted version, unless it is the newest
    -- one again. One place for both ways of approving (the website's door and the Discord button).
    -- A session keeps version 1 and the 19 newest; at the limit the oldest after version 1 goes
    -- to make room. A stored version is never rewritten, and version numbers only go up.
    CREATE FUNCTION dmbot_recap_keep_version(g BIGINT, c TEXT, at_ BIGINT, by BIGINT, now_s BIGINT)
    RETURNS VOID LANGUAGE plpgsql AS $fn$
    DECLARE
        job RECORD;
        h TEXT;
        last_hash TEXT;
        last_version INT;
        total INT;
    BEGIN
        SELECT r.title, r.summary INTO job FROM recap_jobs r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        h := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        SELECT a.text_hash, a.version INTO last_hash, last_version FROM recap_accepted a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_
            ORDER BY a.version DESC LIMIT 1;
        IF FOUND AND last_hash = h THEN
            RETURN;
        END IF;
        SELECT count(*) INTO total FROM recap_accepted a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_;
        IF total >= 20 THEN
            DELETE FROM recap_accepted WHERE guild_id = g AND campaign_id = c
                AND session_at = at_ AND version = (SELECT min(a.version) FROM recap_accepted a
                    WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_
                    AND a.version > 1);
        END IF;
        INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version, text_hash, title,
            summary, accepted_by, accepted_at)
        VALUES (g, c, at_, COALESCE(last_version, 0) + 1, h, job.title, job.summary, by, now_s);
    END
    $fn$;
    REVOKE ALL ON FUNCTION dmbot_recap_keep_version(BIGINT, TEXT, BIGINT, BIGINT, BIGINT)
        FROM PUBLIC;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_keep_version(BIGINT, TEXT, BIGINT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
    + _approve_with_versions()
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
)

SQL = RECAP_ACCEPTED

ISOLATED_TABLES: tuple[str, ...] = ("recap_accepted",)
