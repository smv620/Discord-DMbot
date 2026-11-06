"""0362_deed_kind

What kind of deed a noticed deed was, kept until the DM records it (#3236).
"""

from __future__ import annotations

DEED_KIND = r"""
    -- The kind of a deed the session reader noticed (#3236): one of a fixed list, or empty. The
    -- DM's Record passes it to `add_deed`, which keeps it in the doer's connection for the story
    -- graph's summary. A row from before this has no kind, which reads as "other deeds".
    ALTER TABLE memory_deed_proposals
        ADD COLUMN kind TEXT NOT NULL DEFAULT ''
        CHECK (kind IN ('', 'attack', 'kill', 'rescue', 'save', 'steal', 'help'));
    """

SQL = DEED_KIND
