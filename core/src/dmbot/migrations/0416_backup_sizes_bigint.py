"""0416_backup_sizes_bigint

A backup's size is in bytes. Both sizes were INT (32-bit) and are BIGINT (#4060), so the column can
never be what a size overflows, whatever a file's cap becomes later (a file is held at most 25 MB
now, so this is a defensive change, kept with the rule that every size, id and time is 64-bit).
"""

from __future__ import annotations

# The rewrite takes a short exclusive lock; the rows live at most 15 minutes, so it is harmless.
# Only the type changes: the doors that read `size` build JSON from it (`jsonb_build_object`), and a
# plpgsql row takes whatever type the column has, so none of them needs a rewrite.
SQL = """
    ALTER TABLE web_backups ALTER COLUMN size TYPE BIGINT;
    ALTER TABLE web_backup_parts ALTER COLUMN size TYPE BIGINT;
"""
