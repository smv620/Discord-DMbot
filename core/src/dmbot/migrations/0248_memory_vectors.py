"""0248_memory_vectors

Meaning search: numbers made from campaign text (#2664).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

# Meaning search (#2664; docs/STORY_MEMORY.md 9a, "Embeddings"). `memory_vectors`: one row for
# each piece of the campaign's own text turned into numbers by Voyage AI: the owner's kind and id,
# the model that made them, a hash of the text (so a changed text is made again) and the numbers as
# a plain list, compared in Python inside one campaign (no extension needed). Never in a backup:
# they are made again. A transcript line's row is tied to the line itself, so EVERY way a line goes
# (Stop recording me, Delete my transcripts, retention, a deleted session or campaign) takes its
# numbers in the same statement: no vector of a person's words outlives the words.
MEMORY_VECTORS = """
    CREATE TABLE memory_vectors (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        owner_kind  TEXT NOT NULL CHECK (char_length(owner_kind) BETWEEN 1 AND 40),
        owner_id    TEXT NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 200),
        line_id     BIGINT,
        model       TEXT NOT NULL CHECK (char_length(model) BETWEEN 1 AND 100),
        text_hash   TEXT NOT NULL CHECK (char_length(text_hash) BETWEEN 1 AND 100),
        vector      REAL[] NOT NULL CHECK (cardinality(vector) BETWEEN 1 AND 4096),
        made_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, owner_kind, owner_id),
        -- a line's numbers are tied to the line; no other kind has a line
        CHECK ((owner_kind = 'line') = (line_id IS NOT NULL)),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        -- the line's own key only: a second unique index on a table that grows with every spoken
        -- line would cost the recorder on every insert. `save_lines` takes the line and its guild
        -- from the line's own row, so the two cannot differ.
        FOREIGN KEY (line_id) REFERENCES transcript_lines (id) ON DELETE CASCADE
    );
    -- (deleting a line finds its numbers by index, not a scan)
    CREATE INDEX memory_vectors_by_line ON memory_vectors (line_id) WHERE line_id IS NOT NULL;
    """ + _isolate("memory_vectors")

SQL = MEMORY_VECTORS

ISOLATED_TABLES: tuple[str, ...] = ("memory_vectors",)
