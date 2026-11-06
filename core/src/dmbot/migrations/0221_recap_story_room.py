"""0221_recap_story_room

The recap story column holds 30,000 (#2601).
"""

from __future__ import annotations

RECAP_STORY_ROOM = """
    -- The recap story's column limit goes from 6,000 to 30,000 characters (#2601): a length is a
    -- target in the writer's instructions and never a reason to cut what it wrote, so the column is
    -- only a guard (`fit_summary` shortens past it and logs). The number is
    -- `recaps.fixed_lines.STORY_COLUMN_MAX`; a test keeps the two equal. The constraint is
    -- replaced, no row is rewritten, so no row-level security is touched.
    ALTER TABLE recap_jobs
        DROP CONSTRAINT recap_jobs_summary_check,
        DROP CONSTRAINT recap_jobs_original_check,
        ADD CONSTRAINT recap_jobs_summary_check CHECK (char_length(summary) <= 30000),
        ADD CONSTRAINT recap_jobs_original_check CHECK (char_length(original) <= 30000);
"""

SQL = RECAP_STORY_ROOM
