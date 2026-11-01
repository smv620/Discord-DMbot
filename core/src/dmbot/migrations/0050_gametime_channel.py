"""0050_gametime_channel."""

from __future__ import annotations

GAMETIME_CHANNEL = """
    -- The players' game-time channel (docs/PLAN.md, "TimeBot"; #1116): `dmb-gametime-<slug>`,
    -- everyone reads, only DMbot posts, with the players' pinned clock ("Day 5, morning")
    -- edited in place. Its ids are channel and message ids: they mean nothing in another
    -- server, so (like the DM screen's) none goes into a backup. Nullable columns: no row
    -- changes, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN gametime_channel_id BIGINT;
    ALTER TABLE game_clocks ADD COLUMN players_channel_id BIGINT;
    ALTER TABLE game_clocks ADD COLUMN players_message_id BIGINT;
"""

SQL = GAMETIME_CHANNEL
