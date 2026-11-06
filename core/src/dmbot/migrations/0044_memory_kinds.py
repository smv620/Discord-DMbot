"""0044_memory_kinds."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

MEMORY_KINDS = (
    f"""
    -- One kind for each memory entry (#1034, owner decision 2026-10-10; docs/STORY_MEMORY.md,
    -- "Entity kinds"): an entry's kind says what it fundamentally is (character, place, item,
    -- group, event, idea). Roles and categories are never kinds:
    --   role        player_character, npc or god, on a character, one at a time
    --   needs_look  the entry was moved here from an older kind (a named creature, a spell)
    --               and the DM keeps, links or deletes it; nothing is deleted for them
    -- and a character's species, creature type, stat block, class and background are links
    -- to the rules (memory_rule_links), kept by source and name; a name the rules data
    -- doesn't have keeps its plain words with known = false.
    ALTER TABLE memory_entities ADD COLUMN role TEXT
        CHECK (role IS NULL OR role IN ('player_character', 'npc', 'god'));
    ALTER TABLE memory_entities ADD COLUMN needs_look BOOLEAN NOT NULL DEFAULT FALSE;

    CREATE TABLE memory_rule_links (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id  TEXT NOT NULL,
        kind       TEXT NOT NULL
            CHECK (kind IN ('species', 'creature_type', 'stat_block', 'class', 'background')),
        rules_source TEXT NOT NULL CHECK (char_length(rules_source) <= 40),
        name       TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
        edition    TEXT CHECK (edition IS NULL OR edition IN ('2014', '2024')),
        known      BOOLEAN NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (known = (rules_source <> '')),
        {_entity_link("entity_id")}
    );
    -- One species and one creature type for each character; any number of the others.
    CREATE UNIQUE INDEX memory_rule_links_one_species
        ON memory_rule_links (guild_id, campaign_id, entity_id) WHERE kind = 'species';
    CREATE UNIQUE INDEX memory_rule_links_one_type
        ON memory_rule_links (guild_id, campaign_id, entity_id) WHERE kind = 'creature_type';
    CREATE UNIQUE INDEX memory_rule_links_no_repeat
        ON memory_rule_links (guild_id, campaign_id, entity_id, kind, rules_source, name);
    """
    + _isolate("memory_rule_links")
    + """

    -- Move the older kinds. Migrations run with no server set, so row-level security hides
    -- every row: each table the statements read or write is opened for them, and closed
    -- again (CLAUDE.md, lesson from #704). The core kinds live in code; what is stored is
    -- campaigns' own rows: entries, and extension kinds and relationships that name them.
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_types FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_predicates FOR ALL USING (true) WITH CHECK (true);

    -- An entry of a campaign's own kind that sat under an older kind keeps what that meant:
    -- its role (a player's character stays one, with its player), or a look from the DM for
    -- a creature. Before the kinds' parents are moved below, and before the entries' own
    -- older kinds are, since it reads each entry's kind from memory_types.
    UPDATE memory_entities e SET
        role = CASE t.parent
            WHEN 'player_character' THEN 'player_character'
            WHEN 'npc' THEN 'npc'
            WHEN 'deity' THEN 'god' END,
        needs_look = (t.parent = 'creature')
        FROM memory_types t
        WHERE t.guild_id = e.guild_id AND t.campaign_id = e.campaign_id AND t.key = e.type
          AND t.parent IN ('player_character', 'npc', 'deity', 'creature');

    -- The older kinds, in one pass over the table. A named creature is a character; which
    -- role, if any, is the DM's call. A spell is rules vocabulary, not an entry: kept as an
    -- idea for the DM to look at (nothing is deleted).
    UPDATE memory_entities SET
        role = CASE type
            WHEN 'player_character' THEN 'player_character'
            WHEN 'npc' THEN 'npc'
            WHEN 'deity' THEN 'god' END,
        needs_look = type IN ('creature', 'spell'),
        type = CASE type WHEN 'spell' THEN 'concept' ELSE 'character' END
        WHERE type IN ('player_character', 'npc', 'deity', 'creature', 'spell');

    -- Whoever plays an entry plays a player character (only those were played before).
    UPDATE memory_entities SET role = 'player_character'
        WHERE played_by IS NOT NULL AND role IS NULL;

    -- A campaign's own kinds that sat under an older kind now sit under its replacement,
    -- and its own relationships that named an older kind name the replacement (each once,
    -- in the order they were given).
    UPDATE memory_types SET parent = 'character'
        WHERE parent IN ('player_character', 'npc', 'deity', 'creature');
    UPDATE memory_types SET parent = 'concept' WHERE parent = 'spell';
    UPDATE memory_predicates SET
        subject_types = ARRAY(SELECT x FROM (
            SELECT CASE
                WHEN t IN ('player_character', 'npc', 'deity', 'creature') THEN 'character'
                WHEN t = 'spell' THEN 'concept' ELSE t END AS x, min(ord) AS o
            FROM unnest(subject_types) WITH ORDINALITY AS u(t, ord) GROUP BY 1) q ORDER BY o),
        object_types = ARRAY(SELECT x FROM (
            SELECT CASE
                WHEN t IN ('player_character', 'npc', 'deity', 'creature') THEN 'character'
                WHEN t = 'spell' THEN 'concept' ELSE t END AS x, min(ord) AS o
            FROM unnest(object_types) WITH ORDINALITY AS u(t, ord) GROUP BY 1) q ORDER BY o)
        WHERE subject_types && ARRAY['player_character', 'npc', 'deity', 'creature', 'spell']
           OR object_types && ARRAY['player_character', 'npc', 'deity', 'creature', 'spell'];

    DROP POLICY migrate_backfill ON memory_predicates;
    DROP POLICY migrate_backfill ON memory_types;
    DROP POLICY migrate_backfill ON memory_entities;
    """
)

SQL = MEMORY_KINDS

ISOLATED_TABLES: tuple[str, ...] = ("memory_rule_links",)
