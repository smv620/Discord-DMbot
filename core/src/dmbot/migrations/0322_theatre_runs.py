"""0322_theatre_runs

Whether a script of a session is being made right now (#3244, slice 5b): the database check that
stops a second run, so a restart cannot start one beside a running one.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

THEATRE_RUNS = """
    -- One row for each session: its latest run to make a script. 'running' with a recent
    -- `moved_at` means someone is on it; one that stopped moving for long enough (the bot died) may
    -- be taken over. It holds counts and times only, never a word of a session, and it can be
    -- rebuilt by pressing the button again, so it is never in a backup.
    CREATE TABLE theatre_runs (
        guild_id    BIGINT NOT NULL,
        session_id  TEXT NOT NULL,
        -- which run owns the row: a run that was taken over cannot touch its successor's row
        token       TEXT NOT NULL,
        state       TEXT NOT NULL CHECK (state IN ('running', 'done', 'failed')),
        started_at  BIGINT NOT NULL CHECK (started_at > 0),
        moved_at    BIGINT NOT NULL CHECK (moved_at > 0),
        part        INTEGER NOT NULL DEFAULT 0 CHECK (part >= 0),
        parts       INTEGER NOT NULL DEFAULT 0 CHECK (parts >= 0),
        PRIMARY KEY (guild_id, session_id),
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + _isolate("theatre_runs")

SQL = THEATRE_RUNS

ISOLATED_TABLES: tuple[str, ...] = ("theatre_runs",)
