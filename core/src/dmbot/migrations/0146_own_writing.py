"""0146_own_writing

A DM's own background is its own purpose (#2167).
"""

from __future__ import annotations

OWN_WRITING = """
    -- A DM's own background, read by DMbot for names (#2167), is a different thing from a list of
    -- names (names_list) or a published adventure (shared_story): its own purpose, so the record
    -- of who confirmed what can tell them apart. No row changes.
    ALTER TABLE shared_confirmations DROP CONSTRAINT shared_confirmations_purpose_check;
    ALTER TABLE shared_confirmations ADD CONSTRAINT shared_confirmations_purpose_check
        CHECK (purpose IN ('names_list', 'shared_story', 'rulebook', 'own_writing'));
    """

SQL = OWN_WRITING
