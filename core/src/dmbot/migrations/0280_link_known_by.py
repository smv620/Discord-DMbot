"""0280_link_known_by

A backstory connection that waits for the DM to keep it starts as known by the character whose
backstory it came from (#2935). The column holds that character's entry id, or '' for none.
"""

from __future__ import annotations

LINK_KNOWN_BY = """
    -- The player character whose backstory a waiting connection or question came from (the DM
    -- said "Whose backstory is this?"): kept, the connection starts as known by that character
    -- only (hidden from the party). An entry id, or '' for none: no existing row changes.
    ALTER TABLE backstory_links
        ADD COLUMN known_by TEXT NOT NULL DEFAULT '' CHECK (char_length(known_by) <= 40);
    ALTER TABLE backstory_which
        ADD COLUMN known_by TEXT NOT NULL DEFAULT '' CHECK (char_length(known_by) <= 40);
"""

SQL = LINK_KNOWN_BY
