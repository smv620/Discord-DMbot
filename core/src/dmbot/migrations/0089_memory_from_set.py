"""0089_memory_from_set

Sets carry relationship definitions (#1456).
"""

from __future__ import annotations

MEMORY_FROM_SET = """
    -- A relationship definition borrowed from a followed shared rule set (#1456; docs/PLAN.md,
    -- "Shared rulesets"): the set's id, null for the campaign's own and for the official ones.
    -- Read-only while it is the set's. A column with no default and no backfill: every existing
    -- row is the campaign's own, which null already says, so no row changes and no row-level
    -- security needs opening.
    ALTER TABLE memory_predicates ADD COLUMN from_set TEXT
        CHECK (from_set IS NULL OR from_set ~ '^[0-9a-f]{32}$');
    """

SQL = MEMORY_FROM_SET
