"""0090_support_reason

Why a ticket went to a person (#1459).
"""

from __future__ import annotations

SUPPORT_REASON = """
    -- Why a ticket was handed to a person (#1459): one short code set where the helper escalates
    -- it, shown on the admin page. Null for a ticket that was never escalated or has been
    -- answered since. No backfill: tickets escalated before this have no recorded reason, which
    -- null already says, so no row changes and no row-level security needs opening.
    ALTER TABLE support_tickets ADD COLUMN escalated_reason TEXT
        CHECK (escalated_reason IS NULL OR escalated_reason IN
               ('unhappy', 'asked', 'limit', 'no-email', 'helper-down'));
    """

SQL = SUPPORT_REASON
