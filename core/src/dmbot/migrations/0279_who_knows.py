"""0279_who_knows

Which characters know an other name (#2935).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WHO_KNOWS = r"""
    -- "Who knows this?" for an other name (#2935): a name the DM keeps from the table (a secret
    -- spelling) can be known by some characters. The characters are the points of view the story
    -- memory already has (`memory_perspectives`, kind `pc`); a connection known by some characters
    -- is a belief of theirs in `memory_beliefs`, and a name has no confidence or history, so here
    -- it is one row for each (other name, character). The name stays secret for every public
    -- reader (table answers, recaps, the cleaner's hints for others): this table is read only for
    -- the DM and for a player whose own character is listed. In the undo log (an Undo takes a
    -- change of who knows back, and an Undo of removing a name brings its knowers back); the
    -- database also removes a row with its name or its character. ON UPDATE CASCADE like the
    -- tables a move rewrites (#2550).
    CREATE TABLE memory_alias_knowers (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id             TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        alias_id       TEXT NOT NULL,
        perspective_id TEXT NOT NULL,
        created_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, alias_id, perspective_id),
        FOREIGN KEY (guild_id, campaign_id, alias_id)
            REFERENCES memory_aliases (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, perspective_id)
            REFERENCES memory_perspectives (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_alias_knowers_by_perspective
        ON memory_alias_knowers (guild_id, campaign_id, perspective_id);
    """ + _isolate("memory_alias_knowers")

SQL = WHO_KNOWS

ISOLATED_TABLES: tuple[str, ...] = ("memory_alias_knowers",)
