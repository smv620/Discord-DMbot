"""0131_recap_to_confirm

Names a recap kept, for the DM to confirm (#2027).
"""

from __future__ import annotations

RECAP_TO_CONFIRM = """
    -- Names a recap kept that no list had, for the DM to confirm (#2027): shown under the DM's
    -- copy as "Facts to be confirmed" and queued in the name check. names_state: 0 not yet told,
    -- 1 the DM screen was told, 2 and reminded at the next session. Defaulted columns change no
    -- rows, so no row-level-security policy is needed.
    ALTER TABLE recap_jobs ADD COLUMN to_confirm TEXT[] NOT NULL DEFAULT '{}';
    ALTER TABLE recap_jobs ADD COLUMN names_state INTEGER NOT NULL DEFAULT 0
        CHECK (names_state BETWEEN 0 AND 2);
"""

SQL = RECAP_TO_CONFIRM
