"""0093_transcript_suggestions

A player's suggested fix (#1491).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

TRANSCRIPT_SUGGESTIONS = """
    -- A player's suggested fix to their own line of the cleaned transcript (#1491). The DM
    -- decides; only the line's `text` ever changes, never what was heard. `started_ms` and
    -- `user_id` name the line (like transcript_lines). One open suggestion for a line at a
    -- time; one that waits past the end of the next session is `dropped` (no message). A new
    -- table, so no existing row changes and no row-level security needs opening.
    CREATE TABLE transcript_suggestions (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id   BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        started_ms BIGINT NOT NULL,
        user_id    BIGINT NOT NULL CHECK (user_id > 0),
        suggested  TEXT NOT NULL CHECK (char_length(suggested) BETWEEN 1 AND 200),
        -- The line's cleaned words when it was suggested: Accept refuses a line that changed since.
        base_text  TEXT NOT NULL CHECK (char_length(base_text) <= 4000),
        state      TEXT NOT NULL DEFAULT 'open'
                       CHECK (state IN ('open', 'accepted', 'declined', 'dropped')),
        created_at BIGINT NOT NULL,
        decided_at BIGINT,
        decided_by BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX transcript_suggestions_one_open
        ON transcript_suggestions (session_id, user_id, started_ms) WHERE state = 'open';
    """ + _isolate("transcript_suggestions")

SQL = TRANSCRIPT_SUGGESTIONS

ISOLATED_TABLES: tuple[str, ...] = ("transcript_suggestions",)
