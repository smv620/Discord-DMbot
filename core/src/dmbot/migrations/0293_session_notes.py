"""0293_session_notes

A session's beat sheet is kept with the session as the campaign's own session notes (#3026). It
survives the deletion of transcript lines and goes only with the campaign.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# A migration that changes rows opens row-level security for itself (CLAUDE.md, #704): both tables
# are written or read below, so each has a `migrate_backfill` policy for the length of the file.
SESSION_NOTES = (
    """
    -- One current beat sheet for each session (`session_at` is the session's start, as in
    -- `recap_jobs`). `beats` is the sheet as the recap pipeline stores it (a JSON list, read back
    -- by dmbot.recaps.beats.from_json); `written_at` is when it was last written, and
    -- `job_created_at` is the `created_at` of the recap job it came from (0 when it came from a
    -- backup). ON UPDATE CASCADE like the tables a move rewrites (#2550); deleted only with the
    -- campaign.
    CREATE TABLE session_notes (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        session_at      BIGINT NOT NULL CHECK (session_at > 0),
        beats           JSONB NOT NULL,
        written_at      BIGINT NOT NULL,
        job_created_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, campaign_id, session_at),
        CHECK (jsonb_typeof(beats) = 'array')
    );
    """
    + _isolate("session_notes")
    + """
    CREATE POLICY migrate_backfill ON session_notes FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON recap_jobs FOR SELECT USING (true);

    -- Every beat sheet a recap job already holds becomes its session's notes.
    INSERT INTO session_notes
            (guild_id, campaign_id, session_at, beats, written_at, job_created_at)
        SELECT j.guild_id, j.campaign_id, j.session_at, j.beats, j.updated_at, j.created_at
        FROM recap_jobs j
        WHERE jsonb_typeof(j.beats) = 'array' AND jsonb_array_length(j.beats) > 0
        ON CONFLICT DO NOTHING;

    DROP POLICY migrate_backfill ON recap_jobs;
    DROP POLICY migrate_backfill ON session_notes;
    """
)

SQL = SESSION_NOTES

ISOLATED_TABLES: tuple[str, ...] = ("session_notes",)
