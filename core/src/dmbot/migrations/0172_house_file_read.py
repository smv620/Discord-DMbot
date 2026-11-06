"""0172_house_file_read

The AI's last read of a linked file (#2377).
"""

from __future__ import annotations

HOUSE_FILE_READ = """
    -- The linked house-rules file read by the AI (#2377): a hash of the file's text as it was
    -- when last read (spacing and case ignored), so the AI is asked again only when the file
    -- changes. Never the text. A new link forgets it. Added with no default, so no row changes.
    ALTER TABLE house_rules_file ADD COLUMN read_fingerprint TEXT
        CHECK (read_fingerprint IS NULL OR read_fingerprint ~ '^[0-9a-f]{64}$');
"""

SQL = HOUSE_FILE_READ
