"""0024_transcript_topics."""

from __future__ import annotations

TRANSCRIPT_TOPICS = """
    -- The off-topic filter (#52): each line's topic (only off_topic is hidden, in the
    -- cleaned transcript; the as-heard one keeps everything) and how long it was said,
    -- for the "[1m 22s of off-topic chat skipped]" marker. Old lines: game, 0 ms.
    ALTER TABLE transcript_lines
        ADD COLUMN topic TEXT NOT NULL DEFAULT 'game'
            CHECK (topic IN ('game', 'table_talk', 'off_topic')),
        ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0);
    """

SQL = TRANSCRIPT_TOPICS
