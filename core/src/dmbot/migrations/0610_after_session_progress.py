"""0610_after_session_progress

Where a worker's after-session block has got to, for the DM's card (#4905 slice 4b).
"""

from __future__ import annotations

SQL = r"""
    -- What the worker's block is on, so the bot's card can say "part 2 of 5" while the worker
    -- reads (CLAUDE.md: every wait shows that DMbot is still working). A stage code (the story
    -- read, the story threads, or any other step), the part it is on and of how many, and when it
    -- last moved: codes, counts and a time, never a word of the session. The worker sets them
    -- under its lease; the bot only reads them.
    ALTER TABLE after_session_jobs
        ADD COLUMN stage    TEXT NOT NULL DEFAULT ''
            CHECK (stage IN ('', 'story', 'threads', 'working')),
        ADD COLUMN part     INTEGER NOT NULL DEFAULT 0 CHECK (part >= 0),
        ADD COLUMN parts    INTEGER NOT NULL DEFAULT 0 CHECK (parts >= 0),
        ADD COLUMN moved_at BIGINT NOT NULL DEFAULT 0;
    """
