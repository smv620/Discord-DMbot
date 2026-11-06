"""0018_payment_event_subscription."""

from __future__ import annotations

PAYMENT_EVENT_SUBSCRIPTION = """
    -- Which subscription each recorded payment event was about (#435). Deletion cancels at
    -- the period's end, so an old subscription's last news can arrive after the person has
    -- signed up again; knowing it was theirs before lets it be ignored, not retried.
    -- Rows from before this have no subscription id, so they keep the old retry (no real
    -- payment company was connected then, so there are none).
    ALTER TABLE payment_events ADD COLUMN subscription_id TEXT;
"""

SQL = PAYMENT_EVENT_SUBSCRIPTION
