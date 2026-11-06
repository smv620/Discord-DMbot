"""0033_usage_grace."""

from __future__ import annotations

USAGE_GRACE = """
    -- The hours meter's cap finish (#437 part 2): when a session reaches the cap, it may
    -- run on for up to 2 hours, once a month. This records which session was given that
    -- grace, so a restart doesn't give it twice or take it away, and no other session gets
    -- it. A new column on an existing table: no rows change.
    ALTER TABLE owner_hours ADD COLUMN grace_session BIGINT;
    """

SQL = USAGE_GRACE
