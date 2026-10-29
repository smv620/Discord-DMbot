"""0004_campaign_memory."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

_MEMORY_TABLES = (
    "memory_types",
    "memory_predicates",
    "memory_entities",
    "memory_aliases",
    "memory_mentions",
    "memory_relations",
    "memory_corrections",
    "memory_flags",
    "memory_changes",
)

CAMPAIGN_MEMORY = f"""
    -- Campaign memory (docs/PLAN.md, "Campaign memory (EntityBot)", #126): a property
    -- graph in plain tables. EntityBot (dmbot.memory) is the only writer, and every
    -- write is logged in memory_changes so it can be undone.

    -- Bumped by every change, so copies held in memory know when to reload.
    ALTER TABLE campaigns ADD COLUMN memory_version BIGINT NOT NULL DEFAULT 0;

    -- Campaign-only additions to the memory rules. The fixed core lives in code
    -- (dmbot.memory.ontology). Terms are never deleted, only deprecated.
    CREATE TABLE memory_types (
        {_memory_scope()}
        key         TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{{1,39}}$'),
        parent      TEXT NOT NULL,
        label       TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
        examples    TEXT[] NOT NULL,
        reason      TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
        status      TEXT NOT NULL CHECK (status IN ('active', 'deprecated')),
        replaced_by TEXT,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, key)
    );

    CREATE TABLE memory_predicates (
        {_memory_scope()}
        key             TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{{1,39}}$'),
        parent          TEXT,
        label           TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
        description     TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
        examples        TEXT[] NOT NULL,
        reason          TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
        subject_types   TEXT[] NOT NULL,
        object_types    TEXT[] NOT NULL,
        is_symmetric    BOOLEAN NOT NULL,
        max_per_subject INTEGER CHECK (max_per_subject IS NULL OR max_per_subject >= 1),
        conflicts_with  TEXT[] NOT NULL,
        status          TEXT NOT NULL CHECK (status IN ('active', 'deprecated')),
        replaced_by     TEXT,
        created_at      BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, key)
    );

    CREATE TABLE memory_entities (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        type        TEXT NOT NULL,
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        description TEXT NOT NULL,
        status      TEXT NOT NULL
            CHECK (status IN ('proposed', 'confirmed', 'rejected', 'merged')),
        merged_into TEXT,
        source      TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((status = 'merged') = (merged_into IS NOT NULL)),
        FOREIGN KEY (guild_id, campaign_id, merged_into)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            DEFERRABLE INITIALLY DEFERRED
    );

    CREATE TABLE memory_aliases (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id   TEXT NOT NULL,
        text        TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 100),
        key         TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 100),
        kind        TEXT NOT NULL
            CHECK (kind IN ('full', 'short', 'nickname', 'title', 'misheard')),
        used_by     TEXT,
        secret      BOOLEAN NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
        sound_codes TEXT[] NOT NULL,
        source      TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, entity_id, key),
        {_entity_link("entity_id")},
        {_entity_link("used_by", cascade=False)}
    );
    CREATE INDEX memory_aliases_by_key ON memory_aliases (guild_id, campaign_id, key);
    CREATE INDEX memory_aliases_by_used_by ON memory_aliases (guild_id, campaign_id, used_by)
        WHERE used_by IS NOT NULL;
    CREATE INDEX memory_entities_by_merged_into
        ON memory_entities (guild_id, campaign_id, merged_into) WHERE merged_into IS NOT NULL;

    -- "This part of this line refers to this entity." Lines will get their own table
    -- with the transcript channel (#124); until then a mention names its line.
    CREATE TABLE memory_mentions (
        {_memory_scope()}
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id          TEXT NOT NULL,
        session_started_at BIGINT,
        line_ref           TEXT NOT NULL CHECK (length(line_ref) <= 100),
        span_start         INTEGER NOT NULL CHECK (span_start >= 0),
        span_end           INTEGER NOT NULL CHECK (span_end > span_start),
        confidence         DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        method             TEXT NOT NULL
            CHECK (method IN ('exact', 'sound', 'spelling', 'context', 'dm')),
        created_at         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        {_entity_link("entity_id")}
    );
    CREATE INDEX memory_mentions_by_entity ON memory_mentions (guild_id, campaign_id, entity_id);

    -- Facts are never overwritten: a change (ally to enemy) ends one and adds another.
    -- Times are when the fact holds: session start times now, game time with TimeBot.
    CREATE TABLE memory_relations (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        subject_id      TEXT NOT NULL,
        predicate       TEXT NOT NULL,
        object_id       TEXT NOT NULL,
        detail          TEXT NOT NULL CHECK (length(detail) <= 100),
        confidence      DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        status          TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
        source          TEXT NOT NULL,
        mention_ids     TEXT[] NOT NULL,
        from_session_at BIGINT,
        to_session_at   BIGINT,
        from_game_time  BIGINT,
        to_game_time    BIGINT,
        secret          BOOLEAN NOT NULL,
        created_at      BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (subject_id <> object_id),
        {_entity_link("subject_id")},
        {_entity_link("object_id")}
    );
    CREATE INDEX memory_relations_by_subject
        ON memory_relations (guild_id, campaign_id, subject_id);
    CREATE INDEX memory_relations_by_object
        ON memory_relations (guild_id, campaign_id, object_id);

    -- What a word heard should become ("fix"), or must stay ("keep": an Undo or
    -- "Keep as heard" became a "don't change this" rule).
    CREATE TABLE memory_corrections (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        heard      TEXT NOT NULL CHECK (length(heard) BETWEEN 1 AND 100),
        heard_key  TEXT NOT NULL CHECK (length(heard_key) BETWEEN 1 AND 100),
        entity_id  TEXT,
        action     TEXT NOT NULL CHECK (action IN ('fix', 'keep')),
        source     TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((action = 'fix') = (entity_id IS NOT NULL)),
        {_entity_link("entity_id")}
    );
    CREATE INDEX memory_corrections_by_key
        ON memory_corrections (guild_id, campaign_id, heard_key);
    CREATE INDEX memory_corrections_by_entity
        ON memory_corrections (guild_id, campaign_id, entity_id) WHERE entity_id IS NOT NULL;

    -- A failed rule check, kept for review: never fixed silently.
    CREATE TABLE memory_flags (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        kind        TEXT NOT NULL
            CHECK (kind IN ('wrong_subject', 'wrong_object', 'too_many', 'contradiction')),
        relation_id TEXT NOT NULL,
        other_id    TEXT,
        status      TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, other_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE INDEX memory_flags_by_relation ON memory_flags (guild_id, campaign_id, relation_id);
    CREATE INDEX memory_flags_by_other ON memory_flags (guild_id, campaign_id, other_id)
        WHERE other_id IS NOT NULL;

    -- Append-only log of every change, so any operation can be undone. One operation
    -- (a "batch") may change several rows. Not part of backups. The one exception: the quote
    -- kept with a name read from the DM's story (#2167) is blanked here too once the DM decides
    -- on the name, so none of the DM's text outlives the decision.
    CREATE TABLE memory_changes (
        {_memory_scope()}
        version    BIGINT NOT NULL,
        batch      BIGINT NOT NULL,
        table_name TEXT NOT NULL,
        row_id     TEXT NOT NULL,
        op         TEXT NOT NULL CHECK (op IN ('insert', 'update', 'delete')),
        before     JSONB,
        after      JSONB,
        source     TEXT NOT NULL,
        undoes     BIGINT,
        made_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, version)
    );
    CREATE INDEX memory_changes_by_batch ON memory_changes (guild_id, campaign_id, batch);
    CREATE INDEX memory_changes_by_undo ON memory_changes (guild_id, campaign_id, undoes)
        WHERE undoes IS NOT NULL;
    """ + "".join(_isolate(t) for t in _MEMORY_TABLES)

SQL = CAMPAIGN_MEMORY

ISOLATED_TABLES: tuple[str, ...] = (
    "memory_types",
    "memory_predicates",
    "memory_entities",
    "memory_aliases",
    "memory_mentions",
    "memory_relations",
    "memory_corrections",
    "memory_flags",
    "memory_changes",
)
