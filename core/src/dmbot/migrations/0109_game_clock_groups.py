"""0109_game_clock_groups

Split-party clocks (#1607).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

GAME_CLOCK_GROUPS = f"""
    -- Split-party clocks (#1607; docs/PLAN.md, "TimeBot"): while the party is split, each group
    -- has its own clock here, and `game_clocks` keeps the clock from before the split (it is
    -- the clock again when the party rejoins). Per campaign, never shared between campaigns or
    -- servers; deleted with the campaign; in its backups (position, name, time, last rest).
    -- At most three groups, changed only by a DM of the campaign (checked in the same
    -- transaction). A new table and nullable or defaulted columns: no row changes, so no
    -- backfill policy.
    CREATE TABLE game_clock_groups (
        {_memory_scope()}
        position       INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
        name           TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 20),
        minute         BIGINT NOT NULL CHECK (minute >= 0),
        last_long_rest BIGINT NOT NULL CHECK (last_long_rest >= 0),
        tired_told_for BIGINT CHECK (tired_told_for IS NULL OR tired_told_for >= 0),
        combat_round   INTEGER CHECK (combat_round IS NULL OR combat_round BETWEEN 1 AND 99),
        combat_secs    INTEGER NOT NULL DEFAULT 0 CHECK (combat_secs BETWEEN 0 AND 59),
        PRIMARY KEY (guild_id, campaign_id, position)
    );
    {_isolate("game_clock_groups")}
    -- picked_group: the group the buttons act on (remembered across restarts); NULL when the
    -- party is not split. The two told flags make the "split, so I didn't move the clock" line
    -- and the "9 hours ahead" line each be said once per split. split_undo is the snapshot
    -- the last Split or Rejoin's Undo needs (a token, and what to put back).
    ALTER TABLE game_clocks
        ADD COLUMN picked_group INTEGER
            CHECK (picked_group IS NULL OR picked_group BETWEEN 1 AND 3),
        ADD COLUMN split_phrase_told BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN split_drift_told BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN split_undo JSONB;
    -- A timer started while split counts on the group picked then (NULL: the first group, or
    -- the one clock when the party is not split).
    ALTER TABLE game_effects
        ADD COLUMN group_position INTEGER
            CHECK (group_position IS NULL OR group_position BETWEEN 1 AND 3);
"""

SQL = GAME_CLOCK_GROUPS

ISOLATED_TABLES: tuple[str, ...] = ("game_clock_groups",)
