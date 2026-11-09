"""0403_inferred_links

A connection DMbot inferred from lore, with its reason (#2959 item 5).
"""

from __future__ import annotations

# Supervisor's ruling on #2959 slice 2 (2026-11-09): the lore read may suggest a relationship the
# text implies but doesn't state, marked as DMbot's inference with a short reason. It waits like
# any connection and is never kept without the DM's press. `inferred_reason` is "" for a stated
# connection, so the existing rows need no change; it rides with the waiting row and, once the DM
# keeps it, with the kept connection's source (`memory_link_sources`), so the DM's "Why?" says
# DMbot inferred it. The reason is DMbot's own words about the DM's text: DM-only, like the label.
SQL = r"""
    ALTER TABLE backstory_links ADD COLUMN inferred_reason TEXT NOT NULL DEFAULT ''
        CONSTRAINT backstory_links_inferred_reason_check
        CHECK (char_length(inferred_reason) <= 80);
    ALTER TABLE memory_link_sources ADD COLUMN inferred_reason TEXT NOT NULL DEFAULT ''
        CONSTRAINT memory_link_sources_inferred_reason_check
        CHECK (char_length(inferred_reason) <= 80);
    """
