"""0117_standing

Impressions, attitudes and the party (#1655).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

STANDING = (
    f"""
    -- Story memory 5b, slice 4a (#1655; docs/STORY_MEMORY.md, section 5): standing the DM sets.
    --   memory_entities.party   the campaign's party, a group whose members are worked out in code
    --                           from its confirmed player characters. At most one a campaign.
    --   memory_impressions      how a viewer (an NPC, a group, a place) feels about a subject (a
    --                           character, the party, a group, a people): a score -100..100 and the
    --                           weight of the evidence behind it. A new one for the same pair
    --                           closes the old (closed_at), so the history is kept, like
    --                           memory_states. Sparse: a row only where the DM said something.
    --   memory_attitudes        the same for a group or place toward a whole people or kind.
    -- The `people` kind and the `of_people` connection are core (in code), not rows. Like every
    -- memory table both are one campaign's, with forced row-level security.
    ALTER TABLE memory_entities ADD COLUMN party BOOLEAN NOT NULL DEFAULT false;
    CREATE UNIQUE INDEX memory_entities_one_party
        ON memory_entities (guild_id, campaign_id) WHERE party;
    CREATE TABLE memory_impressions (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        viewer_id       TEXT NOT NULL,
        subject_id      TEXT NOT NULL,
        score           INTEGER NOT NULL CHECK (score BETWEEN -100 AND 100),
        weight          INTEGER NOT NULL CHECK (weight BETWEEN 1 AND 100),
        source          TEXT NOT NULL,
        from_session_at BIGINT,
        created_at      BIGINT NOT NULL,
        closed_at       BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (viewer_id <> subject_id),
        {_entity_link("viewer_id")},
        {_entity_link("subject_id")}
    );
    CREATE UNIQUE INDEX memory_impressions_one_open
        ON memory_impressions (guild_id, campaign_id, viewer_id, subject_id)
        WHERE closed_at IS NULL;
    CREATE INDEX memory_impressions_by_subject
        ON memory_impressions (guild_id, campaign_id, subject_id);
    CREATE TABLE memory_attitudes (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        holder_id       TEXT NOT NULL,
        toward_id       TEXT NOT NULL,
        score           INTEGER NOT NULL CHECK (score BETWEEN -100 AND 100),
        weight          INTEGER NOT NULL CHECK (weight BETWEEN 1 AND 100),
        source          TEXT NOT NULL,
        from_session_at BIGINT,
        created_at      BIGINT NOT NULL,
        closed_at       BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (holder_id <> toward_id),
        {_entity_link("holder_id")},
        {_entity_link("toward_id")}
    );
    CREATE UNIQUE INDEX memory_attitudes_one_open
        ON memory_attitudes (guild_id, campaign_id, holder_id, toward_id)
        WHERE closed_at IS NULL;
    CREATE INDEX memory_attitudes_by_target
        ON memory_attitudes (guild_id, campaign_id, toward_id);
    """
    + _isolate("memory_impressions")
    + _isolate("memory_attitudes")
)

SQL = STANDING

ISOLATED_TABLES: tuple[str, ...] = (
    "memory_impressions",
    "memory_attitudes",
)
