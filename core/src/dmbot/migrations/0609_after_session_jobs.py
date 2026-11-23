"""0609_after_session_jobs

A live session's slow after-session reads run in the worker (#4905 slice 4a, Supervisor's ruling).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- One row for each block of a live session's after-session reads that the bot hands to the
    -- worker (`DMBOT_WORKER`), so the AI calls and the database work run there and a restart
    -- carries on. Ids, times, a lease and a state only: never a word of the session, a name or a
    -- question. `block` is "reads" (the story read and what depends on it) or "late" (importance
    -- and the story threads). The lease (`claimed_by`, `lease_at`) is how two workers, or a worker
    -- and the bot's own fallback, never both take one row; a lease nobody has renewed for a while
    -- can be taken again.
    CREATE TABLE after_session_jobs (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        session_id  TEXT NOT NULL,
        block       TEXT NOT NULL CHECK (block IN ('reads', 'late')),
        dm_ids      BIGINT[] NOT NULL,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'running', 'done', 'failed')),
        claimed_by  TEXT NOT NULL DEFAULT '' CHECK (char_length(claimed_by) <= 40),
        lease_at    BIGINT NOT NULL DEFAULT 0,
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        finished_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_id, block)
    );
    CREATE INDEX after_session_jobs_open ON after_session_jobs (guild_id, state)
        WHERE state IN ('waiting', 'running');
    """ + _isolate("after_session_jobs")

ISOLATED_TABLES: tuple[str, ...] = ("after_session_jobs",)
