"""0119_homebrew_more_kinds

Species, subclass, background (#1663).
"""

from __future__ import annotations

# Homebrew gains the species, subclass and background kinds (#1663): D&D Beyond's homebrew has
# them and the owner's live check found no way to pick them. Only the kind checks widen, no row
# changes, so no migration policy is needed.
MORE_HOMEBREW_KINDS = """
    ALTER TABLE homebrew_entries DROP CONSTRAINT homebrew_entries_kind_check;
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_entries_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'other',
                        'species', 'subclass', 'background'));
    ALTER TABLE homebrew_pages DROP CONSTRAINT homebrew_pages_kind_check;
    ALTER TABLE homebrew_pages ADD CONSTRAINT homebrew_pages_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'other',
                        'species', 'subclass', 'background'));
    ALTER TABLE pack_suggestions DROP CONSTRAINT pack_suggestions_kind_check;
    ALTER TABLE pack_suggestions ADD CONSTRAINT pack_suggestions_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'rule', 'other',
                        'species', 'subclass', 'background'));
"""

SQL = MORE_HOMEBREW_KINDS
