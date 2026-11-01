"""0141_recap_holds

A recap waits for the DM's name answers (#2169).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RECAP_HOLDS = f"""
    -- A session's recap held until the DM has answered DMbot's name questions (#2169). A hold is
    -- a row here, not a job state: the recap job is made when the hold is released (the last
    -- question answered, or "Write the recap now"), so the job list and the workers do not
    -- change and nothing costs anything while it waits. One per session. Per campaign, isolated,
    -- deleted with the campaign, and left out of backups (it is a wait, not content).
    CREATE TABLE recap_holds (
        {_memory_scope()}
        session_at  BIGINT NOT NULL,
        session_id  TEXT NOT NULL,
        dm_user_id  BIGINT NOT NULL CHECK (dm_user_id > 0),
        channel_id  BIGINT NOT NULL,
        created_at  BIGINT NOT NULL,
        -- The DM has been told on the DM screen. A hold made while the story extractor still had
        -- names to propose is silent until it is done: there may be nothing to ask.
        asked       BOOLEAN NOT NULL DEFAULT TRUE,
        PRIMARY KEY (guild_id, campaign_id, session_at)
    );
    """ + _isolate("recap_holds")

SQL = RECAP_HOLDS

ISOLATED_TABLES: tuple[str, ...] = ("recap_holds",)
