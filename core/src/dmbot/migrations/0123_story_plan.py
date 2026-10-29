"""0123_story_plan

The story plan read from the adventure (#1714).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

STORY_PLAN = (
    f"""
    -- The story plan (docs/STORY_MEMORY.md, section 6; #1714): what the DM's written adventure
    -- expects, read from its text by the AI once, when the DM asks. A layer of its own, never
    -- mixed with the table's facts (memory_*) and never shown to players. Every table is
    -- isolated per server and campaign. The nodes, links and read records are deleted with the
    -- document that produced them (ON DELETE CASCADE) and with the campaign, and are NOT in
    -- backups (the text isn't either): after a restore the file is given again and read again.
    -- `plan_spend` is the exception to "goes with the document": it is a ledger of what reading
    -- cost, kept by the file's fingerprint (the SHA-256 of its text), so removing a file and
    -- giving it again can't get round the caps. A row is a reservation of the most a call could
    -- cost until the call returns, then its real cost. It goes with the campaign. New tables, no
    -- existing rows.
    CREATE TABLE adventure_reads (
        {_memory_scope()}
        document_id TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('reading', 'done', 'stopped')),
        next_chunk  INTEGER NOT NULL CHECK (next_chunk >= 0),
        total_chunks INTEGER NOT NULL CHECK (total_chunks > 0),
        stop_reason TEXT NOT NULL DEFAULT '' CHECK (char_length(stop_reason) <= 40),
        skipped_parts INTEGER NOT NULL DEFAULT 0 CHECK (skipped_parts >= 0),
        started_by  BIGINT NOT NULL CHECK (started_by > 0),
        started_at  BIGINT NOT NULL,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, document_id),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE plan_nodes (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('chapter', 'place', 'person')),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
        name_key    TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 120),
        summary     TEXT NOT NULL CHECK (char_length(summary) <= 500),
        quote       TEXT NOT NULL CHECK (char_length(quote) <= 200),
        chunk_ids   TEXT[] NOT NULL CHECK (cardinality(chunk_ids) <= 8),
        position    INTEGER NOT NULL CHECK (position >= 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, document_id, kind, name_key),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    -- "From needs to": Chapter 3 needs Captain Varn (alive). `need` is what the book expects of
    -- the second end, in a few words, or empty.
    CREATE TABLE plan_links (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        from_id     TEXT NOT NULL,
        to_id       TEXT NOT NULL,
        need        TEXT NOT NULL CHECK (char_length(need) <= 120),
        quote       TEXT NOT NULL CHECK (char_length(quote) <= 200),
        chunk_ids   TEXT[] NOT NULL CHECK (cardinality(chunk_ids) <= 8),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, from_id, to_id, need),
        CHECK (from_id <> to_id),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, from_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, to_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE plan_spend (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        month       TEXT NOT NULL CHECK (month ~ '^[0-9]{{4}}-[0-9]{{2}}$'),
        micro_usd   BIGINT NOT NULL CHECK (micro_usd >= 0),
        input_tokens  INTEGER NOT NULL CHECK (input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
        at          BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX plan_spend_month ON plan_spend (guild_id, campaign_id, month);
    CREATE INDEX plan_nodes_document ON plan_nodes (guild_id, campaign_id, document_id, kind);
    """
    + _isolate("adventure_reads")
    + _isolate("plan_nodes")
    + _isolate("plan_links")
    + _isolate("plan_spend")
)

SQL = STORY_PLAN

ISOLATED_TABLES: tuple[str, ...] = (
    "adventure_reads",
    "plan_nodes",
    "plan_links",
    "plan_spend",
)
