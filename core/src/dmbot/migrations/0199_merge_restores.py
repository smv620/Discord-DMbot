"""0199_merge_restores

Where a merge moved notes and a sheet (#2438).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

MERGE_RESTORES = f"""
    -- Where a merge moved a name's private notes and D&D Beyond sheet (#2438). Both live outside
    -- the change log (a note is its writer's own, a sheet is refreshed by its player), so Undo had
    -- no way to hand them back to the name they came from. One row per merged-away name per
    -- batch: lengths and times of the notes and the sheet row as they were before the merge, never
    -- a note's words (a person who deletes their notes leaves no copy). Undo reads it,
    -- puts back only what is still as the merge left it, and never refuses because of it. Kept
    -- as long as the batch is (pruned with it), deleted with the campaign.
    CREATE TABLE merge_restores (
        {_memory_scope()}
        id       BIGSERIAL,
        batch    BIGINT NOT NULL,
        -- 'undo': what an Undo of the batch puts back; 'redo': the batch is an Undo, and undoing
        -- it merges these two again (the notes and sheet follow).
        kind     TEXT NOT NULL CHECK (kind IN ('undo', 'redo')),
        gone_id  TEXT NOT NULL,
        keep_id  TEXT NOT NULL,
        before   JSONB NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX merge_restores_by_batch ON merge_restores (guild_id, campaign_id, batch);
    """ + _isolate("merge_restores")

SQL = MERGE_RESTORES

ISOLATED_TABLES: tuple[str, ...] = ("merge_restores",)
