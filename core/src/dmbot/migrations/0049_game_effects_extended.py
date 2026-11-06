"""0049_game_effects_extended."""

from __future__ import annotations

GAME_EFFECTS_EXTENDED = """
    -- The minutes the DM added with "Still going" (#1112), kept apart from `minutes` (how long
    -- it was timed for) so the "has likely ended" line can show the total and say it was
    -- extended. A column with a default: no existing row changes, so no backfill policy.
    ALTER TABLE game_effects ADD COLUMN extended INTEGER NOT NULL DEFAULT 0
        CHECK (extended >= 0);
"""

SQL = GAME_EFFECTS_EXTENDED
