"""0310_game_clock_group_log

Which player character was in which split-party group, and when (#3131).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

GAME_CLOCK_GROUP_LOG = """
    -- While the party is split each group has its own game clock, and a line of speech gets the
    -- game time of the group its speaker was in when it was spoken (`timebot/stamp.py`). This is
    -- the history of who was where: one row each time the DM places a player character in a group
    -- (`position` 1 to 3), moves them, or the groups end (`position` NULL: in no group now). A
    -- character's group at a moment is their newest row at or before it, so an older line keeps
    -- the group its speaker was in then, whatever later splits did. Append-only; the rows are
    -- ids, numbers and a real time, never a word. A character that is erased takes its rows.
    -- Not carried in backups (the reason is in campaigns/tables.py).
    CREATE TABLE game_clock_group_log (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        seq          BIGINT GENERATED ALWAYS AS IDENTITY,
        character_id TEXT NOT NULL,
        position     INTEGER CHECK (position IS NULL OR position BETWEEN 1 AND 3),
        at           BIGINT NOT NULL CHECK (at > 0),
        PRIMARY KEY (guild_id, campaign_id, seq),
        FOREIGN KEY (guild_id, campaign_id, character_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX game_clock_group_log_by_character
        ON game_clock_group_log (guild_id, campaign_id, character_id, seq);
    """ + _isolate("game_clock_group_log")

SQL = GAME_CLOCK_GROUP_LOG

ISOLATED_TABLES: tuple[str, ...] = ("game_clock_group_log",)
