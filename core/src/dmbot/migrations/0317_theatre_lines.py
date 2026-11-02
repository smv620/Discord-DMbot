"""0317_theatre_lines

The theatrical transcript's lines, a DM-only working copy of a session (#3244).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

THEATRE_LINES = """
    -- A session recast as a script with every reference resolved in brackets (#3242, #3244). It is
    -- made from the cleaned transcript by an AI and can be made again, so it is never in a backup.
    -- Only the campaign's DM may read it (a page and a download of the DM's own; never a player).
    -- Its words are people's words, so it goes with them: `user_ids` are everyone whose lines it
    -- was made from, and "Delete my transcripts" removes every row that lists the person; a
    -- session's retention and the session itself remove its rows. `source_ids` are the
    -- transcript lines it came from (the lineage), as plain numbers: a row is saved only when
    -- every one of them still exists, and the store re-checks that in the same transaction.
    CREATE TABLE theatre_lines (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id   BIGINT NOT NULL,
        session_id TEXT NOT NULL,
        position   INTEGER NOT NULL CHECK (position >= 0),
        -- the one who says the line (a user who agreed to be recorded)
        user_id    BIGINT NOT NULL,
        user_ids   BIGINT[] NOT NULL CHECK (cardinality(user_ids) BETWEEN 1 AND 50),
        source_ids BIGINT[] NOT NULL CHECK (cardinality(source_ids) BETWEEN 1 AND 200),
        text       TEXT NOT NULL CHECK (text <> ''),
        made_at    BIGINT NOT NULL CHECK (made_at > 0),
        UNIQUE (guild_id, session_id, position),
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- (forgetting a person finds their rows by their id)
    CREATE INDEX theatre_lines_by_person ON theatre_lines USING gin (user_ids);
    """ + _isolate("theatre_lines")

SQL = THEATRE_LINES

ISOLATED_TABLES: tuple[str, ...] = ("theatre_lines",)
