"""0104_homebrew_book_edition

Which rules a book is for (#1563).
"""

from __future__ import annotations

HOMEBREW_BOOK_EDITION = """
    -- Which rules a shared book is for (#1563): 2024, 2014 or other (a supplement), asked when a DM
    -- loads a file and says it is from a published book. It decides where the entry ranks next to
    -- the free rules (a 2014 book entry sits with the fallback, not above the 2024 book). NULL for
    -- an entry that is not from a book, and for one loaded before this (read as "other"). A
    -- nullable column with no default: no existing row changes, so no row-level security needs
    -- opening.
    ALTER TABLE homebrew_entries ADD COLUMN book_edition TEXT
        CHECK (book_edition IS NULL OR book_edition IN ('2024', '2014', 'other'));
    -- Only a shared book's entry carries an edition (the store and backups already refuse more).
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_edition_needs_book
        CHECK (book_edition IS NULL OR from_book);
    """

SQL = HOMEBREW_BOOK_EDITION
