"""0304_belief_game_time

The game time a belief was made at (#3091, part 3).
"""

from __future__ import annotations

BELIEF_GAME_TIME = """
    -- Game minutes since the start of Day 1 (the game clock's own unit, `timebot/clock.py`) when
    -- the belief was made LIVE in a session that had a clock. NULL is "time not known": every
    -- belief made after a session, from a reading, an import, lore or the DM's edit, and every
    -- belief from before this column. Never the clock as it reads when the row is written later.
    -- A number only: no calendar words and no real-world timestamp (the calendar is words over
    -- this number). Nothing else about beliefs changes.
    ALTER TABLE memory_beliefs
        ADD COLUMN game_time BIGINT CHECK (game_time IS NULL OR game_time >= 0);
"""

SQL = BELIEF_GAME_TIME

ISOLATED_TABLES: tuple[str, ...] = ()
