"""0083_memory_said_as

A spelling for the narrator (#1419).
"""

from __future__ import annotations

MEMORY_SAID_AS = """
    -- A spelling for the recap's narrator (#1419): "Kah-zeth" for Ka'zeth. Set by the DM on a name,
    -- through the same change path as every other edit (so it can be undone), and only on a name
    -- that is not secret: the voice company is sent these, and a secret name never leaves DMbot.
    -- The check makes the database refuse the pair even if code forgot.
    ALTER TABLE memory_aliases ADD COLUMN said_as TEXT
        CHECK (said_as IS NULL OR char_length(said_as) BETWEEN 1 AND 80);
    ALTER TABLE memory_aliases ADD CONSTRAINT memory_aliases_secret_not_said
        CHECK (NOT secret OR said_as IS NULL);
    """

SQL = MEMORY_SAID_AS
