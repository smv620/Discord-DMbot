"""0404_npc_guesses

DMbot's guesses of which NPCs and groups know a lore statement, waiting for the DM (#2959).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Supervisor's ruling on #2959 slice 3 (STORY_MEMORY 9b item 4): with DMBOT_STORY_NPC_GUESS on, a
# lore read guesses which of its NPCs and groups know each name and connection, with how sure. A
# guess is written as a knower only once the statement is confirmed (never 1.0, so it reads as a
# guess, the way the Smart guess's 0.75 does; the DM's own edit writes 1.0 and so clears the mark).
# Until then it waits: a name's guesses in `memory_npc_guesses` (applied when the DM confirms the
# name), a connection's on its waiting row (`backstory_links.npc_guesses`, applied when the DM keeps
# it). Ids and a number only. Not carried in a backup (`campaigns/tables.py`): a guess that never
# became a knower is made again by the next read. No row is changed.
SQL = r"""
    CREATE TABLE memory_npc_guesses (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id      TEXT NOT NULL,
        knower_id      TEXT NOT NULL,
        confidence     REAL NOT NULL CHECK (confidence > 0 AND confidence < 1),
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_npc_guesses_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, entity_id, knower_id),
        CHECK (entity_id <> knower_id),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, knower_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_npc_guesses_knower ON memory_npc_guesses (guild_id, campaign_id, knower_id);
    CREATE TRIGGER memory_npc_guesses_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_npc_guesses
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    ALTER TABLE backstory_links ADD COLUMN npc_guesses JSONB NOT NULL DEFAULT '{}'::jsonb
        CONSTRAINT backstory_links_npc_guesses_check CHECK (jsonb_typeof(npc_guesses) = 'object');
    """ + _isolate("memory_npc_guesses")

ISOLATED_TABLES: tuple[str, ...] = ("memory_npc_guesses",)
