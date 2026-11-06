"""0038_payment_event_kind."""

from __future__ import annotations

PAYMENT_EVENT_KIND = """
    -- What each recorded payment event was (#922): the 7-day grace after a failed payment
    -- is only for someone who has paid before, and that is read from here. Rows from
    -- before this have no kind and count as paid (the writer reads NULL that way), so
    -- anyone recorded earlier keeps grace and still lapses when the plan ends. There is
    -- no backfill: nothing here changes a row.
    ALTER TABLE payment_events ADD COLUMN kind TEXT;
"""

SQL = PAYMENT_EVENT_KIND
