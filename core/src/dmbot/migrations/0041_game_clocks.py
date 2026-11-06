"""0041_game_clocks."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

GAME_CLOCKS = f"""
    -- A campaign's game clock (#965; docs/PLAN.md, "TimeBot"): game time, not real time,
    -- set and moved only by the DM's buttons and clear phrases. One row per campaign, and
    -- none until the DM sets it. Per campaign, never shared between campaigns or servers;
    -- deleted with the campaign; in its backups (without the message ids).
    --   minute          game minutes since the start of Day 1
    --   last_long_rest  the game minute the party last finished a long rest
    --   tired_told_for  the long-rest minute the 24-hours-without-a-rest line was said for
    --   channel_id, message_id  the pinned clock message on the DM screen, edited in place
    CREATE TABLE game_clocks (
        {_memory_scope()}
        minute         BIGINT NOT NULL CHECK (minute >= 0),
        last_long_rest BIGINT NOT NULL CHECK (last_long_rest >= 0),
        tired_told_for BIGINT CHECK (tired_told_for IS NULL OR tired_told_for >= 0),
        channel_id     BIGINT,
        message_id     BIGINT,
        updated_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id)
    );
    """ + _isolate("game_clocks")

SQL = GAME_CLOCKS

ISOLATED_TABLES: tuple[str, ...] = ("game_clocks",)
