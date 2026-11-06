"""0298_dormant_knowers

What hiding an entry closes is kept, asleep, and comes back when the entry is shown again (#3082).
"""

from __future__ import annotations

DORMANT_KNOWERS = r"""
    -- `hidden_by`: the entries whose being hidden from the players put this row to sleep. A row
    -- with any is DORMANT: every reader ignores it (the gate, the readers and the website's doors
    -- below), and it wakes when each of them is shown again. Several entries can close one row (a
    -- connection with two hidden ends), so it is a set, and showing them in any order works. On a
    -- belief it marks the 0.0 belief that a hide wrote, so the belief before it can come back.
    -- The marker lives in the row, so the change log's pruning cannot lose it. Logged like every
    -- change to these tables, so Undo still takes a hide or a show back. Rows from earlier hides,
    -- which were deleted, are not recovered.
    ALTER TABLE memory_knowers ADD COLUMN hidden_by TEXT[] NOT NULL DEFAULT '{}';
    ALTER TABLE memory_beliefs ADD COLUMN hidden_by TEXT[] NOT NULL DEFAULT '{}';

    -- The website's helpers and doors that read who knows what must not see a sleeping row: each
    -- reads the table as `memory_knowers k`, which becomes the same table without them. The
    -- functions are read back as they are and rewritten whole (SECURITY DEFINER, the pinned search
    -- path and the grants stay as they were).
    DO $do$
    DECLARE
        f RECORD;
        def text;
    BEGIN
        FOR f IN SELECT p.oid FROM pg_proc p
                 WHERE p.pronamespace = current_schema()::regnamespace
                   AND p.prosrc LIKE '%memory_knowers k%'
        LOOP
            def := pg_get_functiondef(f.oid);
            def := replace(def, 'memory_knowers k',
                           '(SELECT * FROM memory_knowers WHERE cardinality(hidden_by) = 0) k');
            EXECUTE def;
        END LOOP;
    END
    $do$;
"""

SQL = DORMANT_KNOWERS

ISOLATED_TABLES: tuple[str, ...] = ()
