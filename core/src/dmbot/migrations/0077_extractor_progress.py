"""0077_extractor_progress

A restart resumes the extractor (#1367).
"""

from __future__ import annotations

EXTRACTOR_PROGRESS = """
    -- How far the story extractor got through a stored session (#1367), kept on the session's
    -- own row (so it is that campaign's, behind the same row-level security, and goes with the
    -- session). `extracted_through_ms` is when the last line it read began (the lines' own
    -- clock, not a position, so removing a person's lines cannot move it): a restart reads only
    -- lines that began after it and never sends the same lines twice. `extractor_calls` is how
    -- many AI calls the session has used, and `extracted_at` when it last ran, for the
    -- campaign's daily ceiling. All zero for a session never read.
    ALTER TABLE transcript_sessions ADD COLUMN extracted_through_ms BIGINT NOT NULL DEFAULT 0
        CHECK (extracted_through_ms >= 0);
    ALTER TABLE transcript_sessions ADD COLUMN extractor_calls INT NOT NULL DEFAULT 0
        CHECK (extractor_calls >= 0);
    ALTER TABLE transcript_sessions ADD COLUMN extracted_at BIGINT;
"""

SQL = EXTRACTOR_PROGRESS
