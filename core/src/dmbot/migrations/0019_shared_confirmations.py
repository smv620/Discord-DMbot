"""0019_shared_confirmations."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

SHARED_CONFIRMATIONS = f"""
    -- Who confirmed the right to use shared material, and when (CLAUDE.md, IP rule:
    -- "Record who confirmed and when"; #252): one row per confirmation, what it was for
    -- (a names list now; later a shared story or a rulebook), and a SHA-256 fingerprint
    -- of the text that tells documents apart. Never the text or the file's name.
    -- Deleted with the campaign; carried in its backups.
    CREATE TABLE shared_confirmations (
        {_memory_scope()}
        id           TEXT NOT NULL,
        user_id      BIGINT NOT NULL,
        purpose      TEXT NOT NULL CHECK (purpose IN ('names_list', 'shared_story', 'rulebook')),
        fingerprint  TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        confirmed_at BIGINT NOT NULL,
        -- Read from a backup file, not pressed here (anyone may restore one).
        restored     BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """ + _isolate("shared_confirmations")

SQL = SHARED_CONFIRMATIONS

ISOLATED_TABLES: tuple[str, ...] = ("shared_confirmations",)
