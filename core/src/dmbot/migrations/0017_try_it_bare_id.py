"""0017_try_it_bare_id."""

from __future__ import annotations

TRY_IT_BARE_ID = """
    -- try_it_used outlives a deleted account (one free trial per person), so it keeps
    -- only the Discord id: no date (web's decision on #435, privacy page).
    ALTER TABLE try_it_used DROP COLUMN used_at;
"""

SQL = TRY_IT_BARE_ID
