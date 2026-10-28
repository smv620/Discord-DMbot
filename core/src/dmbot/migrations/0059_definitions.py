"""0059_definitions."""

from __future__ import annotations

DEFINITIONS = """
    -- The official relationships, traceable (#1189). The definitions the code ships are kept
    -- here by version, written at every start (a key and version already here are never
    -- rewritten), so a fact can say which definition it was written under. Shared by every
    -- campaign and holding no campaign data: no guild_id, so no row-level security (like
    -- schema_migrations).
    CREATE TABLE ontology_predicate_versions (
        key             TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
        version         INTEGER NOT NULL CHECK (version >= 1),
        label           TEXT NOT NULL,
        inverse_label   TEXT NOT NULL,
        description     TEXT NOT NULL,
        covers          TEXT[] NOT NULL,
        not_this        TEXT[] NOT NULL,
        examples        TEXT[] NOT NULL,
        synonyms        TEXT[] NOT NULL,
        subject_types   TEXT[] NOT NULL,
        object_types    TEXT[] NOT NULL,
        is_symmetric    BOOLEAN NOT NULL,
        created_at      BIGINT NOT NULL,
        PRIMARY KEY (key, version)
    );
    -- A campaign's own relationships carry the same fields, with a version.
    ALTER TABLE memory_predicates
        ADD COLUMN covers        TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN not_this      TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN inverse_label TEXT NOT NULL DEFAULT '',
        ADD COLUMN synonyms      TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN version       INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);
    -- Every fact says which version of its relationship's definition it was written under.
    -- Facts from before were all written under version 1: the column's default gives them
    -- that without an UPDATE, so no row-level security has to be opened for it.
    ALTER TABLE memory_relations
        ADD COLUMN definition_version INTEGER NOT NULL DEFAULT 1 CHECK (definition_version >= 1);
    """

SQL = DEFINITIONS
