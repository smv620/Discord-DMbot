"""0042_game_effects."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

GAME_EFFECTS = f"""
    -- The game clock counts the timers it has numbered, so a number is never used twice even
    -- after a timer is ended (an old "has likely ended" message can't act on a newer timer).
    ALTER TABLE game_clocks ADD COLUMN effects_made INTEGER NOT NULL DEFAULT 0
        CHECK (effects_made >= 0);
    -- Timed effects the DM starts on a campaign's game clock (#998; docs/PLAN.md, "TimeBot"):
    -- Bless, Mage Armor and the like. Per campaign, never shared between campaigns or servers;
    -- deleted with the campaign; in its backups. Only the campaign's DMs start or end them
    -- (dmbot.timebot.effects checks that in the same transaction). Ends are game minutes, so
    -- a restart keeps them. At most 20 per campaign (checked in the same transaction).
    --   told  the "has likely ended" line was said (once); the DM then ends it or extends it
    CREATE TABLE game_effects (
        {_memory_scope()}
        number        INTEGER NOT NULL CHECK (number > 0),
        name          TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        target        TEXT CHECK (target IS NULL OR char_length(target) BETWEEN 1 AND 60),
        minutes       INTEGER NOT NULL CHECK (minutes > 0),
        concentration BOOLEAN NOT NULL DEFAULT FALSE,
        ends_minute   BIGINT NOT NULL CHECK (ends_minute >= 0),
        told          BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (guild_id, campaign_id, number)
    );
    """ + _isolate("game_effects")

SQL = GAME_EFFECTS

ISOLATED_TABLES: tuple[str, ...] = ("game_effects",)
