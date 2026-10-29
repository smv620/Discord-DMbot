"""0097_homebrew_entries

A campaign's own homebrew (#1520).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

HOMEBREW_ENTRIES = f"""
    -- A campaign's own homebrew (#1520; docs/PLAN.md, "Homebrew (part 1)"): spells, monsters,
    -- items, feats, conditions and rules the DM made, in the DM's own words. Per campaign, never
    -- shared between campaigns or servers; deleted with the campaign; in its backups. Only the
    -- campaign's DMs change them (dmbot.rules.homebrew checks that in the same transaction).
    --   name_key          the name, case-folded, unique per campaign
    --   players_may_know  false by default: a homebrew monster is a spoiler, so an entry is
    --                     DM-screen content until the DM says otherwise
    --   version           counts changes, so an edit or removal made from an old view is told
    --                     from one made on the current entry
    -- A new table: no existing row changes, so no row-level security needs opening.
    CREATE TABLE homebrew_entries (
        id               BIGINT GENERATED ALWAYS AS IDENTITY,
        {_memory_scope()}
        kind             TEXT NOT NULL
                             CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition',
                                             'rule', 'other')),
        name             TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        -- (case-folding can make a letter up to three, so an 80-letter name fits in 240)
        name_key         TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 240),
        text             TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
        summary          TEXT CHECK (summary IS NULL OR char_length(summary) BETWEEN 1 AND 160),
        players_may_know BOOLEAN NOT NULL DEFAULT false,
        created_by       BIGINT NOT NULL CHECK (created_by > 0),
        created_at       BIGINT NOT NULL,
        updated_at       BIGINT NOT NULL,
        version          INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        PRIMARY KEY (id),
        UNIQUE (guild_id, campaign_id, name_key)
    );
    """ + _isolate("homebrew_entries")

SQL = HOMEBREW_ENTRIES

ISOLATED_TABLES: tuple[str, ...] = ("homebrew_entries",)
