"""0397_knows_how

How two people know each other (#3881).
"""

from __future__ import annotations

KNOWS_HOW = """
    -- How two people know each other, on a `knows` relationship only (#3881, Supervisor's ruling:
    -- one relationship plus this, in its own column, never packed into the feeling text): 'met'
    -- (they have been face to face), 'aware' (heard of them: a tale, a rumour, a letter, a vision)
    -- or '' (the story doesn't say, shown as "knows of"). It only moves up, except by the DM; the
    -- store keeps that rule. No existing row changes (the default is empty), so no row-level
    -- security needs opening. Carried in the change log, merges (the row is kept) and backups.
    ALTER TABLE memory_relations
        ADD COLUMN knows_how TEXT NOT NULL DEFAULT ''
            CHECK (knows_how IN ('', 'aware', 'met'))
            CHECK (knows_how = '' OR predicate = 'knows');
"""

SQL = KNOWS_HOW
