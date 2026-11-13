"""0465_moved_end_reason

Why a mover's tie to a place ended (#4173 slice 5): 'moved', when a bigger place took over.
"""

from __future__ import annotations

# Supervisor's condition on slice 5 (2026-11-13): a tie ended by the place-size MOVE rule
# (`memory/whereabouts.py`, MOVED) carries its own end reason, so a later link that puts the smaller
# place inside the bigger one can find it and reopen it ("in the bakery", "in Waterdeep", then "the
# bakery is in Waterdeep": they never left). 0422 allowed an end reason on an ended `owns` or
# `carries` only, in unnamed CHECKs; those two are dropped (found by their wording) and named ones
# added that also allow 'moved' on an ended `located_in`. No row changes, so no row-level security
# needs opening.
SQL = r"""
    DO $do$
    DECLARE c TEXT;
    BEGIN
        FOR c IN SELECT conname FROM pg_constraint
                 WHERE conrelid = 'memory_relations'::regclass AND contype = 'c'
                   AND pg_get_constraintdef(oid) LIKE '%end_reason%'
                   AND pg_get_constraintdef(oid) NOT LIKE '%to_session_at%' LOOP
            EXECUTE format('ALTER TABLE memory_relations DROP CONSTRAINT %I', c);
        END LOOP;
    END
    $do$;
    ALTER TABLE memory_relations ADD CONSTRAINT memory_relations_end_reason_check
        CHECK (end_reason IN ('sold', 'gave', 'traded', 'stolen', 'lost', 'moved'));
    ALTER TABLE memory_relations ADD CONSTRAINT memory_relations_end_reason_predicate_check
        CHECK (
            end_reason IS NULL
            OR (end_reason <> 'moved' AND predicate IN ('owns', 'carries'))
            OR (end_reason = 'moved' AND predicate = 'located_in')
        );
"""
