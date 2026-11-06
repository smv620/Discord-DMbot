"""0055_fightbot."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

FIGHTBOT = f"""
    -- FightBot (#1129; docs/PLAN.md, "FightBot"): one row per player per campaign, in that
    -- campaign's own server scope, deleted with the campaign. Kept on its own (not on a
    -- character's sheet row) so that forgetting a sheet, or having a second character, never
    -- turns reminders back on.
    --   never          the player chose Never, or turned FightBot off: no reminders at all
    --   quiet_since    the start (Unix seconds) of the session they said "Quiet until next
    --                  session" in: quiet for that session only, restarts included
    --   snoozed_until  Unix seconds: no reminders before then
    --   last_call_at   Unix seconds of the last reminder DMbot worked out for them, so a
    --                  restart cannot send another within two minutes
    CREATE TABLE fightbot_players (
        {_memory_scope()}
        player_id     BIGINT NOT NULL,
        never         BOOLEAN NOT NULL DEFAULT FALSE,
        quiet_since   BIGINT,
        snoozed_until BIGINT,
        last_call_at  BIGINT,
        PRIMARY KEY (guild_id, campaign_id, player_id)
    );
    """ + _isolate("fightbot_players")

SQL = FIGHTBOT

ISOLATED_TABLES: tuple[str, ...] = ("fightbot_players",)
