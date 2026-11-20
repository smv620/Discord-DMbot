"""0552_session_created_at

When a session's row was made (#4537, party knowledge slice 3b; Supervisor's ruling E on #4537):
a session imported after its characters existed counts them as its party, though their stretches
are dated later than the session itself.
"""

from __future__ import annotations

# `created_at`: when the row was made. A live session's row is made at its first transcript save,
# a minute or so after the table starts, so anyone made a player character before that save counts
# for it; an imported one's when it was imported. NULL for rows from before this column: unknown,
# which counts every player character with a stretch as at that session (the careful side for what
# is read to the table: a recap then says only what all of them know). Set by the default from now
# on; nothing writes it by hand. A number only, never a word of the story.
SQL = r"""
    ALTER TABLE transcript_sessions ADD COLUMN created_at BIGINT;
    ALTER TABLE transcript_sessions
        ALTER COLUMN created_at SET DEFAULT extract(epoch FROM now())::BIGINT;
"""
