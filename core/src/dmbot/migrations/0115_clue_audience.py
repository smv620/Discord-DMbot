"""0115_clue_audience

A clue only some people know (#1649).
"""

from __future__ import annotations

CLUE_AUDIENCE = """
    -- A clue only some people know (#1649; docs/STORY_MEMORY.md, section 2): the DM's switch
    -- on a `clue` entry. Who knows it is its confirmed `knows` facts, so nothing else is
    -- stored. The `clue` kind itself is core (in code), not a row. Off for every existing
    -- entry, so this migration changes no row's meaning.
    ALTER TABLE memory_entities ADD COLUMN audience_limited BOOLEAN NOT NULL DEFAULT false;
"""

SQL = CLUE_AUDIENCE
