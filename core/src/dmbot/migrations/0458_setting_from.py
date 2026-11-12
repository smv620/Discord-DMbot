"""0458_setting_from

Who said a place is the campaign's setting: DMbot, from the adventure, or the DM (#4173 slice 2b).
"""

from __future__ import annotations

# Supervisor's ruling on #4173 (2026-11-12 22:20): a setting DMbot worked out from the shared
# adventure (`memory/setting_worlds.py`) is a guess until the DM keeps it, shown as one, and places
# count as inside it only once kept. `setting_from` says which:
# - 'read': DMbot's guess (`setting` true);
# - 'dm': the DM's own word (`setting` true), every setting made before this migration included;
# - 'declined': the DM said ✖ Not the setting to a guess (`setting` false), so DMbot never guesses
#   again for this campaign (the ux-critic on #4173);
# - NULL: nothing said (`setting` false).
# The check keeps the two in step. Carried by the change log, merges and backups like `setting`.
#
# The backfill changes rows of a table with forced row-level security, so it opens a policy for
# itself and closes it after (CLAUDE.md, the lesson of #704); it reads only memory_entities.
SQL = r"""
    ALTER TABLE memory_entities ADD COLUMN setting_from TEXT
        CHECK (setting_from IN ('read', 'dm', 'declined'));
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_entities SET setting_from = 'dm' WHERE setting;
    DROP POLICY migrate_backfill ON memory_entities;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_setting_from_pair
        CHECK (setting = (setting_from IS NOT NULL AND setting_from <> 'declined'));
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
