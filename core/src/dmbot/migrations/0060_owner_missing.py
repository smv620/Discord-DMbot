"""0060_owner_missing."""

from __future__ import annotations

OWNER_MISSING = """
    -- When DMbot first saw that a campaign's owner is not in the server any more (#1214):
    -- after 7 days the campaign becomes ownerless, as if the account were deleted. NULL when the
    -- owner is here (or not known to be gone). A column with no default: no rows change, so no
    -- row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN owner_missing_since BIGINT;
    """

SQL = OWNER_MISSING
