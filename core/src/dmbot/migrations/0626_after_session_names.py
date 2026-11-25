"""0626_after_session_names

The name step of a live session's end in the worker (#4905 slice 4c).
"""

from __future__ import annotations

# The queue's block list gains `names` (the name pass: the name reader, PlotBot's pass and the name
# scan, from ids and the stored lines), and the row gains `result_ids`: the ids of the names the
# pass proposed, which the bot's "new names" step reads, so the worker hands back ids and never a
# word. The block list's constraint is read back as it is and refused if it is not the one
# expected (as 0615 does for the queue door), then replaced by one that also allows `names`.
SQL = r"""
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conrelid = 'after_session_jobs'::regclass
              AND conname = 'after_session_jobs_block_check';
        IF d IS NULL OR d NOT LIKE '%''reads''%' OR d NOT LIKE '%''late''%'
           OR d LIKE '%''names''%' THEN
            RAISE EXCEPTION 'the block list did not have the expected shape';
        END IF;
        ALTER TABLE after_session_jobs DROP CONSTRAINT after_session_jobs_block_check;
        ALTER TABLE after_session_jobs ADD CONSTRAINT after_session_jobs_block_check
            CHECK (block IN ('reads', 'late', 'names'));
    END
    $do$;
    ALTER TABLE after_session_jobs
        ADD COLUMN result_ids TEXT[] NOT NULL DEFAULT '{}'
            CHECK (cardinality(result_ids) <= 500);
    """
