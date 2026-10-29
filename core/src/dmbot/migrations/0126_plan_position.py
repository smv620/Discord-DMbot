"""0126_plan_position

The chapter the DM says the table is in (#1731).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

PLAN_POSITION = f"""
    -- Where the DM says the table is in their adventure (docs/STORY_MEMORY.md, section 6; #1731,
    -- Guide me): one chapter for each file, the DM's word, which beats DMbot's guess from the
    -- names heard in the last two sessions until the DM clears it. Only ever written by the DM's
    -- press; logged in the memory change log (an Undo works). Gone with its file, its chapter and
    -- the campaign, and NOT in backups, like the plan. New table, no existing rows.
    CREATE TABLE plan_position (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        chapter_id  TEXT NOT NULL,
        set_by      BIGINT NOT NULL CHECK (set_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, document_id),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, chapter_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    """ + _isolate("plan_position")

SQL = PLAN_POSITION

ISOLATED_TABLES: tuple[str, ...] = ("plan_position",)
