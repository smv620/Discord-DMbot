"""0133_recap_style

Examples a DM gives for how recaps sound (#2075).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RECAP_STYLE = f"""
    -- The examples a DM pastes so recaps sound the way they like (#2075). DMbot copies how an
    -- example sounds, never what happens in it. At most 3 a campaign, each at most 600 words
    -- (the store checks both). Per campaign, isolated, deleted with the campaign, and in the
    -- campaign's backup.
    CREATE TABLE recap_style_examples (
        {_memory_scope()}
        -- Never reused, so a button on an old panel can't remove a newer example (#2075).
        id          BIGINT GENERATED ALWAYS AS IDENTITY,
        position    INTEGER NOT NULL CHECK (position >= 0),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 8000),
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """ + _isolate("recap_style_examples")

SQL = RECAP_STYLE

ISOLATED_TABLES: tuple[str, ...] = ("recap_style_examples",)
