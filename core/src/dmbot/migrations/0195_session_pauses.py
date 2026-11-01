"""0195_session_pauses

A restart keeps the pauses (#2466).
"""

from __future__ import annotations

SESSION_PAUSES = """
    -- Off the record (#2466): the pauses that are on, kept with the saved session so a restart
    -- that resumes the session resumes them too. {"people": {"<user id>": <ends at, Unix seconds
    -- or null for until resumed>}, "table": <ends at or null>}. A new column with a default, so no
    -- row changes; the session's row already holds this server's data only.
    ALTER TABLE active_sessions ADD COLUMN paused JSONB NOT NULL DEFAULT '{}';
"""

SQL = SESSION_PAUSES
