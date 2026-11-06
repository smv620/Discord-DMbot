"""0124_plan_same_as

The book's entry is the table's, by the DM's word (#1715).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

PLAN_SAME_AS = f"""
    -- The DM's word that an entry in their adventure and an entry in their table's story are one
    -- and the same (docs/STORY_MEMORY.md, section 6; #1715): "the book's Captain Varn" is "our
    -- Captain Varn". Or that they are not, remembered so it isn't asked again. Only ever written
    -- by the DM's press; DMbot suggests by name (no AI) and never decides. Secret by being the
    -- DM's alone: no recall, transcript or player view reads this table. Logged in the memory
    -- change log (an Undo works) and gone with the plan entry or the table entry. Not in backups,
    -- like the plan. `document_id` is the plan entry's file, kept here for the one-to-one rule.
    CREATE TABLE plan_same_as (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        plan_id     TEXT NOT NULL,
        document_id TEXT NOT NULL,
        entity_id   TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('same', 'not_same')),
        decided_by  BIGINT NOT NULL CHECK (decided_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, plan_id, entity_id),
        FOREIGN KEY (guild_id, campaign_id, plan_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE,
        {_entity_link("entity_id")}
    );
    -- A book entry is the same as at most one table entry, and a table entry as at most one
    -- book entry in each file.
    CREATE UNIQUE INDEX plan_same_as_one_for_the_book
        ON plan_same_as (guild_id, campaign_id, plan_id) WHERE status = 'same';
    CREATE UNIQUE INDEX plan_same_as_one_for_the_table
        ON plan_same_as (guild_id, campaign_id, entity_id, document_id) WHERE status = 'same';
    """ + _isolate("plan_same_as")

SQL = PLAN_SAME_AS

ISOLATED_TABLES: tuple[str, ...] = ("plan_same_as",)
