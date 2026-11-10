"""0416_backup_sizes_bigint

A backup's size is in bytes, and a file over 2 GB would have failed to record in an INT (#4060).
Bigger PDFs (#4001) and long campaigns make that less unlikely, so both sizes are BIGINT.
"""

from __future__ import annotations

# Only the type changes: the doors that read `size` build JSON from it (`jsonb_build_object`), and a
# plpgsql row takes whatever type the column has, so none of them needs a rewrite.
SQL = """
    ALTER TABLE web_backups ALTER COLUMN size TYPE BIGINT;
    ALTER TABLE web_backup_parts ALTER COLUMN size TYPE BIGINT;
"""
