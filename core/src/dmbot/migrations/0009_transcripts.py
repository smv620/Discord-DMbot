"""0009_transcripts."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

TRANSCRIPTS = (
    """
    -- Stored session transcripts (#41, #125): anyone in the server may download them.
    -- One row per session, kept by the campaign and deleted with it. A session that
    -- resumes after a restart keeps its row (same campaign and start time).
    CREATE TABLE transcript_sessions (
        id          TEXT PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        started_at  BIGINT NOT NULL,
        ended_at    BIGINT,
        -- Kept with every save, so listing sessions never reads their lines.
        line_count  INTEGER NOT NULL DEFAULT 0,
        speakers    BIGINT[] NOT NULL DEFAULT '{}',
        UNIQUE (id, guild_id),
        UNIQUE (campaign_id, guild_id, started_at),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX transcript_sessions_recent
        ON transcript_sessions (guild_id, campaign_id, started_at DESC)
        WHERE line_count > 0;

    -- One row per piece of speech, only from people who agreed to be recorded. `heard`
    -- is exactly what speech-to-text wrote and never changes; `text` is the cleaned
    -- version, NULL while it's the same as `heard` (always, until the Transcript
    -- Cleaner, #127). Never DM-screen content.
    -- Speaker names aren't stored: downloads use display names at the time.
    CREATE TABLE transcript_lines (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id   BIGINT NOT NULL,
        session_id TEXT NOT NULL,
        started_ms BIGINT NOT NULL,
        user_id    BIGINT NOT NULL,
        heard      TEXT NOT NULL,
        text       TEXT,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX transcript_lines_order ON transcript_lines (session_id, started_ms, id);
    -- For "Delete my past transcripts" and retention (later).
    CREATE INDEX transcript_lines_speaker ON transcript_lines (guild_id, user_id);
    """
    + _isolate("transcript_sessions")
    + _isolate("transcript_lines")
)

SQL = TRANSCRIPTS

ISOLATED_TABLES: tuple[str, ...] = (
    "transcript_sessions",
    "transcript_lines",
)
