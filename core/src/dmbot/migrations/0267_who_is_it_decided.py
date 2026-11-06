"""0267_who_is_it_decided

How a pronoun was settled (#2790).
"""

from __future__ import annotations

BACKSTORY_DECIDED = """
    -- How a pronoun in a lore read was settled (#2790): fixed words and a rounded score only
    -- ("who-is-it: accepted 95%", "who-is-it: not checked"), never a name or any text. Kept with a
    -- waiting connection or question so the DM sees DMbot's check beside the quote. No existing row
    -- changes (the default is empty).
    ALTER TABLE backstory_links
        ADD COLUMN decided TEXT NOT NULL DEFAULT '' CHECK (char_length(decided) <= 60);
    ALTER TABLE backstory_which
        ADD COLUMN decided TEXT NOT NULL DEFAULT '' CHECK (char_length(decided) <= 60);

    -- The same note on the kept relationship, for the DM's card only (never in a door, a search or
    -- a player's view): NULL unless the relationship came from a pronoun DMbot settled. It is
    -- carried in the change log, merges (the row is kept) and backups.
    ALTER TABLE memory_relations
        ADD COLUMN decided TEXT CHECK (decided IS NULL OR char_length(decided) <= 60);
"""

SQL = BACKSTORY_DECIDED
