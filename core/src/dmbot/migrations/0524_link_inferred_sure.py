"""0524_link_inferred_sure

The reader's "sure" on a connection DMbot worked out (#4636 part 3, Supervisor's ruling): a place
the text lists as one of a region's ("the ten towns: Bryn Shander, Targos, …"). Only such a
connection, `in` from a place to the region, sure, from a list, and with no other parent, is kept
by an upload's sure step; every other worked-out one still waits for the DM's own press.
"""

from __future__ import annotations

LINK_INFERRED_SURE = """
    -- False for every existing row: nothing waiting today changes.
    ALTER TABLE backstory_links ADD COLUMN inferred_sure BOOLEAN NOT NULL DEFAULT false;
"""

SQL = LINK_INFERRED_SURE
