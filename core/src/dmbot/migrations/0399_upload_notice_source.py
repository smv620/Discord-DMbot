"""0399_upload_notice_source

Which upload or import made a notice (#3879): the website finds the notice for one upload by this,
never by "the latest after". Notices written before this have neither column and the website shows
no notice for them.
"""

from __future__ import annotations

# Nullable on purpose. `source_id` is the id the website reads (the lore upload's, or the import
# batch's `import_id`); ids only, no text. Adding nullable columns changes no rows, so no
# row-level-security policy is needed.
SQL = r"""
    ALTER TABLE upload_notices
        ADD COLUMN source_kind TEXT CHECK (source_kind IN ('lore', 'import')),
        ADD COLUMN source_id TEXT CHECK (char_length(source_id) <= 100);
    CREATE INDEX upload_notices_by_source
        ON upload_notices (guild_id, campaign_id, source_kind, source_id);
    """
