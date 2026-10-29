"""0134_recap_style_notes

Style notes and corrections (#2076).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RECAP_STYLE_NOTES = (
    f"""
    -- The standing notes a DM keeps about how recaps should sound (#2076), sorted out of their
    -- feedback and saved only when they press Save. At most 10 a campaign (the store drops the
    -- oldest). Per campaign, isolated, deleted with the campaign, and in its backup.
    CREATE TABLE recap_style_notes (
        {_memory_scope()}
        id          BIGINT GENERATED ALWAYS AS IDENTITY,
        position    INTEGER NOT NULL CHECK (position >= 0),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 300),
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """
    + _isolate("recap_style_notes")
    + """
    -- What the DM said was wrong in one recap or should change in it (#2076): given to the writer
    -- on Try again as facts the DM confirmed, for that recap only. A defaulted column changes no
    -- rows, so no row-level-security policy is needed.
    ALTER TABLE recap_jobs ADD COLUMN corrections TEXT[] NOT NULL DEFAULT '{}';
    """
)

SQL = RECAP_STYLE_NOTES

ISOLATED_TABLES: tuple[str, ...] = ("recap_style_notes",)
