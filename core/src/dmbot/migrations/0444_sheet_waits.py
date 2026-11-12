"""0444_sheet_waits

A sheet link the DM gave in the wizard for a character that has no player yet waits for the player
(#4205; Supervisor's ruling on #4221): the link is linked and read once the character has one.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `character_sheets` keeps a sheet for whoever plays the character (`player_id NOT NULL`, and every
# reader relies on it), so a link with no player cannot go there. It waits here instead: the
# campaign, the entry, and the address already reduced to the one shape DMbot ever fetches
# (`https://www.dndbeyond.com/characters/<number>`, the check below), with the time it was put. No
# sheet, no snapshot, no person's id. The bot reads a waiting row when its entry has a player, and
# removes it whatever the answer. The row goes with the campaign and with the entry.
SQL = r"""
    CREATE TABLE sheet_waits (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id   TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        url         TEXT NOT NULL CHECK (url ~ '^https://www\.dndbeyond\.com/characters/[0-9]{1,12}$'),
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, entity_id)
    );
    CREATE INDEX sheet_waits_by_age ON sheet_waits (guild_id, created_at);
    """ + _isolate("sheet_waits")

ISOLATED_TABLES: tuple[str, ...] = ("sheet_waits",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
