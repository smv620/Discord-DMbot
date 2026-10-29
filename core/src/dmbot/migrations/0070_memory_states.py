"""0070_memory_states

0069 is the website's move code (#1246).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

MEMORY_STATES = (
    f"""
    -- Story memory 5a, slice 1 (#1267; docs/STORY_MEMORY.md, "State facts"):
    --   memory_states         one current condition for a character, item or place (alive, dead,
    --                         intact, destroyed…). A new one closes the old (closed_at), so the
    --                         history is kept with when and who said so. Own table, not a
    --                         relationship. Like every memory table it is one campaign's.
    --   memory_entities.unique_item  an item of which only one exists. NULL: nobody said, so the
    --                         code decides from the name (a proper name means yes). Nullable so
    --                         this migration changes no existing row.
    --   memory_flags.kind     three new kinds from the new checks.
    CREATE TABLE memory_states (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id       TEXT NOT NULL,
        condition       TEXT NOT NULL CHECK (condition IN
            ('alive', 'dead', 'missing', 'captured', 'transformed', 'unknown',
             'intact', 'destroyed', 'lost')),
        from_session_at BIGINT,
        source          TEXT NOT NULL,
        created_at      BIGINT NOT NULL,
        closed_at       BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        {_entity_link("entity_id")}
    );
    -- One current condition for each entry.
    CREATE UNIQUE INDEX memory_states_one_current
        ON memory_states (guild_id, campaign_id, entity_id) WHERE closed_at IS NULL;
    CREATE INDEX memory_states_by_entity ON memory_states (guild_id, campaign_id, entity_id);
    """
    + _isolate("memory_states")
    + """

    ALTER TABLE memory_entities ADD COLUMN unique_item BOOLEAN;

    ALTER TABLE memory_flags DROP CONSTRAINT memory_flags_kind_check;
    ALTER TABLE memory_flags ADD CONSTRAINT memory_flags_kind_check CHECK (kind IN
        ('wrong_subject', 'wrong_object', 'too_many', 'contradiction',
         'held_twice', 'place_loop', 'acts_after_end'));
    """
)

SQL = MEMORY_STATES

ISOLATED_TABLES: tuple[str, ...] = ("memory_states",)
