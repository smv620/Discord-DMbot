"""0283_memory_knowers

Who knows each statement (#2995; STORY_MEMORY §9b). One row for each (statement, character): the
statement is an entry existing under a name, a property of an entry (a state), a connection or an
other name. The gate (`dmbot.memory.gate`) reads this table and nothing else for who may know what.
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

MEMORY_KNOWERS = r"""
    -- Every statement has a list of the characters who know it (§9b). The words ("secret", "hidden
    -- from the players", "partly revealed", "known to the party") are worked out from the list and
    -- the campaign's player characters, never stored. The DM knows everything and is never listed;
    -- a group grants nothing by itself, so "the party" is never a row. A character is a point of
    -- view the story memory already has (`memory_perspectives`, kind pc or npc). Exactly one of
    -- the four statement columns is set, so each row keeps its own foreign key and the database
    -- removes a row with its statement or its character. In the undo log (an Undo takes a change
    -- of who knows back); ON UPDATE CASCADE like the tables a move rewrites (#2550).
    CREATE TABLE memory_knowers (
        guild_id         BIGINT NOT NULL,
        campaign_id      TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id               TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        perspective_id   TEXT NOT NULL,
        about_entity_id  TEXT,
        state_id         TEXT,
        relation_id      TEXT,
        alias_id         TEXT,
        created_at       BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (num_nonnulls(about_entity_id, state_id, relation_id, alias_id) = 1),
        FOREIGN KEY (guild_id, campaign_id, perspective_id)
            REFERENCES memory_perspectives (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, about_entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, state_id)
            REFERENCES memory_states (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, alias_id)
            REFERENCES memory_aliases (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- One row for each (statement, character); the NULL columns make a plain UNIQUE useless, so
    -- one partial index for each kind.
    CREATE UNIQUE INDEX memory_knowers_entry
        ON memory_knowers (guild_id, campaign_id, about_entity_id, perspective_id)
        WHERE about_entity_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_knowers_state
        ON memory_knowers (guild_id, campaign_id, state_id, perspective_id)
        WHERE state_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_knowers_relation
        ON memory_knowers (guild_id, campaign_id, relation_id, perspective_id)
        WHERE relation_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_knowers_alias
        ON memory_knowers (guild_id, campaign_id, alias_id, perspective_id)
        WHERE alias_id IS NOT NULL;
    CREATE INDEX memory_knowers_by_perspective
        ON memory_knowers (guild_id, campaign_id, perspective_id);
    """ + _isolate("memory_knowers")

SQL = MEMORY_KNOWERS

ISOLATED_TABLES: tuple[str, ...] = ("memory_knowers",)
