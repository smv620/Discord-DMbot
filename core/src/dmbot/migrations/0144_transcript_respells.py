"""0144_transcript_respells

Where a fixed name was respelled (#2170).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

TRANSCRIPT_RESPELLS = """
    -- Where a fixed name's old spelling was written as the new one in past cleaned transcripts
    -- (#2170), so one Undo can put it back. Positions only: the character places in the cleaned
    -- line and the name as it was written there; never the words around it, so a line a person
    -- later asks DMbot to forget leaves nothing behind here. Keyed by the memory change that
    -- fixed the spelling (`batch`), and by `run`: a big campaign is done in runs of at most 5000
    -- lines, each with its own Undo. Rows go with their campaign and their session. New table:
    -- no rows change, so no backfill policy.
    CREATE TABLE transcript_respells (
        id          BIGSERIAL PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        batch       BIGINT NOT NULL,
        session_id  TEXT NOT NULL,
        line_id     BIGINT NOT NULL REFERENCES transcript_lines (id) ON DELETE CASCADE,
        run         INTEGER NOT NULL DEFAULT 1,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        old_text    TEXT NOT NULL CHECK (char_length(old_text) BETWEEN 1 AND 200),
        new_text    TEXT NOT NULL CHECK (char_length(new_text) BETWEEN 1 AND 200),
        offsets     INTEGER[] NOT NULL,
        was         TEXT[] NOT NULL,
        made_at     BIGINT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX transcript_respells_batch ON transcript_respells (campaign_id, batch, run);
    """ + _isolate("transcript_respells")

SQL = TRANSCRIPT_RESPELLS

ISOLATED_TABLES: tuple[str, ...] = ("transcript_respells",)
