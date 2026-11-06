"""0053_combat_state."""

from __future__ import annotations

COMBAT_STATE = """
    -- Combat state (#1127; docs/PLAN.md, "Combat state"): in combat or not, and which round,
    -- kept with the game clock so it survives a restart. `combat_round` is NULL out of combat.
    -- `combat_secs` is the game seconds already spent toward the next whole minute (a round
    -- is 6 seconds; the clock itself counts whole minutes). `side_initiative` is a campaign
    -- switch (off): DMbot tracks rounds only, never turn order, either way. Nullable or
    -- defaulted columns: no row changes, so no backfill policy.
    ALTER TABLE game_clocks
        ADD COLUMN combat_round INTEGER
            CHECK (combat_round IS NULL OR combat_round BETWEEN 1 AND 99),
        ADD COLUMN combat_secs INTEGER NOT NULL DEFAULT 0 CHECK (combat_secs BETWEEN 0 AND 59);
    ALTER TABLE campaigns ADD COLUMN side_initiative BOOLEAN NOT NULL DEFAULT FALSE;
"""

SQL = COMBAT_STATE
