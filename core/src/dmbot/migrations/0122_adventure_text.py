"""0122_adventure_text

The DM's adventure text, in chunks (#1698).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

ADVENTURE_TEXT = (
    f"""
    -- The DM's adventure text (docs/STORY_MEMORY.md, section 6, "Where the story plan comes from";
    -- #1698; CLAUDE.md, IP rule): a file the DM gave DMbot for one campaign, split into chunks so a
    -- later slice (the story-plan reader) can use it. Nothing reads it yet. The text stays in
    -- its campaign: row-level security is forced, both tables are deleted with the campaign
    -- (ON DELETE CASCADE) and a document's chunks go with the document. `fingerprint` is the
    -- SHA-256 of the text, so the same file twice is refused. New tables, no existing rows.
    CREATE TABLE adventure_documents (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        file_name   TEXT NOT NULL CHECK (char_length(file_name) BETWEEN 1 AND 200),
        fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        characters  INTEGER NOT NULL CHECK (characters > 0),
        chunks      INTEGER NOT NULL CHECK (chunks >= 0),  -- 0: restored from a backup, no text
        added_by    BIGINT NOT NULL CHECK (added_by > 0),
        added_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, fingerprint)
    );
    CREATE TABLE adventure_chunks (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        position    INTEGER NOT NULL CHECK (position >= 0),
        heading     TEXT NOT NULL CHECK (char_length(heading) <= 200),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 4000),
        created_at  BIGINT NOT NULL,
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, document_id, position),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    """
    + _isolate("adventure_documents")
    + _isolate("adventure_chunks")
)

SQL = ADVENTURE_TEXT

ISOLATED_TABLES: tuple[str, ...] = (
    "adventure_documents",
    "adventure_chunks",
)
