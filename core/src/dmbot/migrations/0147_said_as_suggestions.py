"""0147_said_as_suggestions

Say it this way? (#2171).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

SAID_AS_SUGGESTIONS = f"""
    -- "Say it this way?" (#2171): when a confirmed name is again and again heard as the same
    -- other spelling, DMbot offers that spelling for the recap narrator's "said as". One row per
    -- name, so the DM is asked once: a "no" stays as state 'no' and is never offered again.
    -- Words only: no speaker, no line, no time of day. It goes with the name and with the
    -- campaign, is not part of backups (a wait, not content), and is isolated per server.
    CREATE TABLE said_as_suggestions (
        {_memory_scope()}
        entity_id  TEXT NOT NULL,
        heard      TEXT NOT NULL CHECK (length(heard) BETWEEN 1 AND 80),
        times      INTEGER NOT NULL CHECK (times > 0),
        state      TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'no')),
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        {_entity_link("entity_id")}
    );
    """ + _isolate("said_as_suggestions")

SQL = SAID_AS_SUGGESTIONS

ISOLATED_TABLES: tuple[str, ...] = ("said_as_suggestions",)
