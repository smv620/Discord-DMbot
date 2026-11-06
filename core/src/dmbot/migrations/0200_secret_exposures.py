"""0200_secret_exposures

Secrets said at the table, recorded (#2410).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

SECRET_EXPOSURES = f"""
    -- Secrets said or typed at the table, waiting for the DM to be told (#2410, docs/PLAN.md,
    -- "Secrets said out loud are revealed too, and the DM can recant"). One row for each secret
    -- name or secret connection found in a session's recorded table talk. Recorded and not
    -- applied: nothing becomes known to the players here, and the summary that tells the DM
    -- applies it later. Holds which secret and how often, never a line or who said it. A name is
    -- kept as its spelling key, a connection as the connection's id. 'settled' once the DM
    -- answered it another way. Transient state, so it is left out of backups.
    CREATE TABLE secret_exposures (
        {_memory_scope()}
        id                 BIGSERIAL,
        session_started_at BIGINT NOT NULL,
        kind               TEXT NOT NULL CHECK (kind IN ('name', 'connection')),
        spelling           TEXT CHECK (spelling IS NULL OR char_length(spelling) BETWEEN 1 AND 400),
        relation_id        TEXT,
        times              INTEGER NOT NULL CHECK (times > 0),
        status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'settled')),
        created_at         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((kind = 'name' AND spelling IS NOT NULL AND relation_id IS NULL)
            OR (kind = 'connection' AND relation_id IS NOT NULL AND spelling IS NULL)),
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX secret_exposures_name
        ON secret_exposures (guild_id, campaign_id, session_started_at, spelling)
        WHERE kind = 'name';
    CREATE UNIQUE INDEX secret_exposures_connection
        ON secret_exposures (guild_id, campaign_id, session_started_at, relation_id)
        WHERE kind = 'connection';
    """ + _isolate("secret_exposures")

SQL = SECRET_EXPOSURES

ISOLATED_TABLES: tuple[str, ...] = ("secret_exposures",)
