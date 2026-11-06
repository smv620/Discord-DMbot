"""0312_memory_importance

An internal importance rank for characters, one assessment at a time (#3172).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

MEMORY_IMPORTANCE = """
    -- How much a character matters to the story, 0 (background) to 5 (player character), as the
    -- story shows it (docs/STORY_MEMORY.md, "Importance"). It only decides which details a recap or
    -- PlotBot picks; it states no fact and is never shown to a player, a card or a recap. One row
    -- each time PlotBot's reading of a session (or a rule) assesses a character; the newest row is
    -- the current assessment, and a rank is never read below the floor the code works out (a
    -- player character, a character heard in two sessions). Append-only, like the rest of the
    -- graph: the history says when a character became important. `basis` is one of a fixed list
    -- of reason codes, never a word of the story; `lines` are line numbers of the session, and a
    -- person's erased words leave the rank alone. Not carried in backups (campaigns/tables.py).
    CREATE TABLE memory_importance (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        seq                BIGINT GENERATED ALWAYS AS IDENTITY,
        entity_id          TEXT NOT NULL,
        rank               SMALLINT NOT NULL CHECK (rank BETWEEN 0 AND 5),
        basis              TEXT NOT NULL CHECK (basis IN (
            'background', 'met_a_pc', 'recurring', 'quest_target', 'quest_giver', 'mentor',
            'rival', 'henchman', 'mini_boss', 'companion', 'antagonist', 'ruler_or_god',
            'lore_figure', 'corrected'
        )),
        source             TEXT NOT NULL CHECK (source IN ('plotbot', 'rule')),
        session_started_at BIGINT,
        lines              INTEGER[] NOT NULL DEFAULT '{}' CHECK (cardinality(lines) <= 20),
        recorded_at        BIGINT NOT NULL CHECK (recorded_at > 0),
        PRIMARY KEY (guild_id, campaign_id, seq),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_importance_by_entity
        ON memory_importance (guild_id, campaign_id, entity_id, seq);
    """ + _isolate("memory_importance")

SQL = MEMORY_IMPORTANCE

ISOLATED_TABLES: tuple[str, ...] = ("memory_importance",)
