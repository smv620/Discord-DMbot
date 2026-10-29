"""0121_story_mode

The Shared story switch (#1692).
"""

from __future__ import annotations

STORY_MODE = """
    -- The Shared story switch (docs/STORY_MEMORY.md, section 6; #1692): Off (the default),
    -- Watch the story or Guide me. Nothing reads it yet: the story-plan reader, the divergence
    -- warnings and Guide me are the next slices. A defaulted column changes no rows, so no
    -- row-level-security policy is needed. The DM's confirmation of the right to use the
    -- material is a row in shared_confirmations (purpose 'shared_story'), not a column.
    ALTER TABLE campaigns
        ADD COLUMN story_mode TEXT NOT NULL DEFAULT 'off'
            CHECK (story_mode IN ('off', 'watch', 'guide'));
"""

SQL = STORY_MODE
