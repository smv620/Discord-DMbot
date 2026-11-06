"""0344_session_split

A session's split into talk, catch-up, last time's recap and play, kept once per session (#3298).
"""

from __future__ import annotations

SESSION_SPLIT = """
    -- The split of a session's lines (recaps/split.py `Split.to_json()`: labels, line numbers and a
    -- method word, never a word of the session). NULL: not made yet. The story reader leaves the
    -- lines it labels as last time's recap out of what it claims, and the recap reads the same
    -- value instead of asking the AI again. It can always be rebuilt, so it is not in a backup.
    ALTER TABLE transcript_sessions ADD COLUMN recap_split JSONB;
"""

SQL = SESSION_SPLIT
