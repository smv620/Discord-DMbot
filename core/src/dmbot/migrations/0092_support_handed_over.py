"""0092_support_handed_over

Every hand-over, kept (#1487).
"""

from __future__ import annotations

SUPPORT_HANDED_OVER = """
    -- Every hand-over, kept for good (#1487). `escalated_reason` says why a ticket is waiting for
    -- a person now and is cleared when one answers; these two are set at the same moment the
    -- helper hands the ticket over and never cleared, so the week's count of hand-overs stays
    -- complete and the admin page can say how long one has waited. No backfill: tickets handed
    -- over before this have neither, which null already says, so no row changes and no
    -- row-level security needs opening.
    ALTER TABLE support_tickets
        ADD COLUMN handed_over_reason TEXT
            CHECK (handed_over_reason IS NULL OR handed_over_reason IN
                   ('unhappy', 'asked', 'limit', 'no-email', 'helper-down')),
        ADD COLUMN handed_over_at BIGINT;
    """

SQL = SUPPORT_HANDED_OVER
