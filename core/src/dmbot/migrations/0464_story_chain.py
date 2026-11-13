"""0464_story_chain

Where the story is, kept from session to session: one chain of open places for the party and one
for each split group (#4173 slice 4).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Supervisor's ruling on #4173 (2026-11-13 04:14): after each session (and import), the DM's own
# lines move a chain of open places, widest first (`memory/session_chain.py`), and it carries over
# to the next session. `chain` is 'main' for the party together, or 's<split start>g<position>' for
# a split group (`game_clock_log`: the split's own start, so a position a later split reuses never
# inherits an old chain). `rank` is the place's order on its chain, 0 the widest. Working state
# only: never shown, never sent to the website. It goes with the campaign (and moves with it: the
# foreign keys cascade), and with a place that is deleted. Ids only, never a word.
SQL = r"""
    CREATE TABLE memory_story_chain (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        chain       TEXT NOT NULL CHECK (chain ~ '^(main|s[0-9]{1,12}g[1-3])$'),
        rank        SMALLINT NOT NULL CHECK (rank BETWEEN 0 AND 20),
        entity_id   TEXT NOT NULL,
        updated_at  BIGINT NOT NULL CHECK (updated_at > 0),
        PRIMARY KEY (guild_id, campaign_id, chain, rank),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + _isolate("memory_story_chain")

ISOLATED_TABLES: tuple[str, ...] = ("memory_story_chain",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
