"""0142_plan_pending

A plan change set for the period's end (#2165).
"""

from __future__ import annotations

PLAN_PENDING = """
    -- A plan change the payment company has scheduled for the end of the paid period (a downgrade
    -- or upgrade picked on its billing page, #2165): the plan that starts, and when. Filled from
    -- the company's schedule events and cleared when the change begins, is undone, or the plan
    -- ends. Two nullable columns: no rows change, so no row-level-security policy is needed.
    ALTER TABLE entitlements ADD COLUMN next_plan TEXT;
    ALTER TABLE entitlements ADD COLUMN next_plan_on BIGINT;
    -- Schedule events are ordered by their own clock, never by `last_event_at`: at a downgrade's
    -- start the schedule's "released" and the plan change arrive in the same moment, and a later
    -- schedule event must not make the real plan change look stale.
    ALTER TABLE entitlements ADD COLUMN next_plan_event_at BIGINT NOT NULL DEFAULT 0;
    -- When the person pressed Renew (our clock). Only a "cancelled" event older than this is
    -- ignored; it does not touch `last_event_at`, which is the payment company's clock alone.
    ALTER TABLE entitlements ADD COLUMN renewed_at BIGINT;
"""

SQL = PLAN_PENDING
