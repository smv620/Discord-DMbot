"""0289_link_world

A lore connection that waits for the DM to keep it can start as known by every player character
(#2959): the upload's "World general knowledge" choice, or the AI's call, statement by statement,
under "Smart guess".
"""

from __future__ import annotations

LINK_WORLD = """
    -- How the upload's choice says the players know this connection once the DM keeps it: 0 not
    -- at all (B, today's behaviour: no existing row changes), 1 the DM's word (A), 2 the AI's
    -- guess (C; written at 0.75). The question rows carry it too, so the answer to a "Which
    -- Mara?" keeps the same choice.
    ALTER TABLE backstory_links ADD COLUMN world SMALLINT NOT NULL DEFAULT 0;
    ALTER TABLE backstory_which ADD COLUMN world SMALLINT NOT NULL DEFAULT 0;
"""

SQL = LINK_WORLD
