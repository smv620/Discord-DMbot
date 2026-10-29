"""0125_plan_divergences

The table has left the adventure (#1729).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

PLAN_DIVERGENCES = f"""
    -- Where the DM's table has left the DM's adventure (docs/STORY_MEMORY.md, section 6; #1729):
    -- the adventure says "Chapter 3 needs Captain Varn alive", the DM said the book's Varn is the
    -- table's Varn, and the table says he is dead. One row for each such clash and each
    -- condition it saw, so the DM is told once. `state_id` is the table's condition row at the
    -- time; it has no foreign key because an Undo deletes that row, and the next check marks the
    -- clash 'gone' (so does a condition that changed again, or a book link taken off). `open` was
    -- shown, `known` is the DM's "I know". Derived from the plan and the table, so it is gone with
    -- the plan link or the table entry, and is NOT in backups, like the plan. New table, no
    -- existing rows.
    CREATE TABLE plan_divergences (
        {_memory_scope()}
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        plan_link_id TEXT NOT NULL,
        entity_id    TEXT NOT NULL,
        state_id     TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('open', 'known', 'gone')),
        created_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, plan_link_id, state_id),
        FOREIGN KEY (guild_id, campaign_id, plan_link_id)
            REFERENCES plan_links (guild_id, campaign_id, id) ON DELETE CASCADE,
        {_entity_link("entity_id")}
    );
    -- (The two indexes below are for the cascades, so deleting a plan entry or a table entry
    -- doesn't scan the other tables; the check itself is served by the unique keys.)
    CREATE INDEX plan_links_to ON plan_links (guild_id, campaign_id, to_id);
    CREATE INDEX plan_same_as_entity ON plan_same_as (guild_id, campaign_id, entity_id);
    """ + _isolate("plan_divergences")

SQL = PLAN_DIVERGENCES

ISOLATED_TABLES: tuple[str, ...] = ("plan_divergences",)
