"""0073_memory_claims."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

MEMORY_CLAIMS = (
    """
    -- Story memory 5a, slice 2 (#1297; docs/STORY_MEMORY.md, section 1): what the story
    -- extractor read out of a session, held as proposals until the DM says yes or no. One row
    -- for each claim, as text (the memory may not know the names yet), with how it was said, the
    -- transcript lines it cites, who spoke them (all of them: consent is checked again when the
    -- DM answers, and a person who stops being recorded has their claims deleted), which prompt
    -- and model made it, and its status: proposed, confirmed, rejected or dropped (the rules
    -- refused it; the reason is kept). A claim is never a fact: a confirmed one has been written
    -- through the store as the DM's own, and fact_id says which (the relationship's id, or the
    -- entry for a change of state), so a fact can always be traced back to the claim and the
    -- lines it came from. Not in the undo log. Like every memory table it is one campaign's.
    CREATE TABLE memory_claims (
        """
    + _memory_scope()
    + """
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        subject            TEXT NOT NULL,
        relationship       TEXT NOT NULL,
        object             TEXT NOT NULL,
        details            TEXT NOT NULL DEFAULT '',
        how                TEXT NOT NULL CHECK (how IN ('dm_said', 'player_said', 'plan')),
        lines              TEXT[] NOT NULL,
        speaker_ids        BIGINT[] NOT NULL DEFAULT '{}',
        source             TEXT NOT NULL,
        prompt_version     TEXT NOT NULL,
        model              TEXT NOT NULL,
        status             TEXT NOT NULL CHECK (status IN
            ('proposed', 'confirmed', 'rejected', 'dropped')),
        reason             TEXT NOT NULL DEFAULT '',
        session_started_at BIGINT,
        created_at         BIGINT NOT NULL,
        decided_at         BIGINT,
        fact_id            TEXT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX memory_claims_waiting ON memory_claims (guild_id, campaign_id, status);
    """
    + _isolate("memory_claims")
    + """
    """
)

SQL = MEMORY_CLAIMS

ISOLATED_TABLES: tuple[str, ...] = ("memory_claims",)
