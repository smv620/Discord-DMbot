"""0099_homebrew_from_book

Homebrew from a confirmed book (#1524).
"""

from __future__ import annotations

HOMEBREW_FROM_BOOK = """
    -- Homebrew loaded from a file after the DM said some of it is from a published book and
    -- confirmed the right to use it (#1524; CLAUDE.md, IP rule): that text stays in this one
    -- campaign and can never go into a shared ruleset, so a later pack must be able to tell it.
    -- A column with a default: no existing row changes, so no row-level security needs opening.
    ALTER TABLE homebrew_entries ADD COLUMN from_book BOOLEAN NOT NULL DEFAULT false;
    """

SQL = HOMEBREW_FROM_BOOK
