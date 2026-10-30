"""0289_link_world

A lore connection that waits for the DM to keep it can start as known by every player character
(#2959): the upload's "World general knowledge" choice, or the AI's call, statement by statement,
under "Smart guess".
"""

from __future__ import annotations

LINK_WORLD = """
    -- True when the upload's choice (A, or C where the AI called it common knowledge) says every
    -- player character knows this connection once the DM keeps it. False: nobody does (B, today's
    -- behaviour: no existing row changes). The question rows carry it too, so the answer to a
    -- "Which Mara?" keeps the same choice.
    ALTER TABLE backstory_links ADD COLUMN world BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE backstory_which ADD COLUMN world BOOLEAN NOT NULL DEFAULT false;
"""

SQL = LINK_WORLD
