"""0028_character_sheets."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

CHARACTER_SHEETS = f"""
    -- A player character's D&D Beyond sheet (#723; docs/PLAN.md, "D&D Beyond character
    -- sheets"): its link, and a small snapshot of names and numbers only (built by
    -- dmbot.memory.sheets, an allow list: never descriptions or rules text). One per
    -- character per campaign, never shared between campaigns; deleted with the
    -- character or the campaign; in backups. Not in the undo log: a refresh isn't a
    -- change the DM undoes. source: 'dndbeyond' (read from the link) or 'typed' (the
    -- player's own form, with no link).
    CREATE TABLE character_sheets (
        {_memory_scope()}
        entity_id  TEXT NOT NULL,
        url        TEXT CHECK (url ~ '^https://www[.]dndbeyond[.]com/characters/[0-9]{{1,12}}$'),
        sheet      JSONB CHECK (sheet IS NULL OR octet_length(sheet::text) <= 65536),
        source     TEXT CHECK (source IN ('dndbeyond', 'typed')),
        fetched_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        CHECK (url IS NOT NULL OR sheet IS NOT NULL),
        CHECK ((sheet IS NULL) = (source IS NULL) AND (sheet IS NULL) = (fetched_at IS NULL)),
        {_entity_link("entity_id")}
    );
    """ + _isolate("character_sheets")

SQL = CHARACTER_SHEETS

ISOLATED_TABLES: tuple[str, ...] = ("character_sheets",)
