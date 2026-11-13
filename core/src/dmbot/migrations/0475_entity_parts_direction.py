"""0475_entity_parts_direction

An entry's search text follows the DM's own direction for a connection (#4164, a follow-up to
#4154).
"""

from __future__ import annotations

# `dmbot_entity_parts` hands each connection's `symmetric` to `memory/entity_text.py`, which leaves
# a one-way connection off the other end's text. It took that from the kind alone, so a rivalry the
# DM made one way still read "is a rival of Cerric" on Wit's entry, and a `sworn_to` the DM made two
# way never showed on the second entry: search and the AI disagreed with the card. #4154's mark
# (`memory_relations.both_ways`, NULL for the kind's default) now wins when it is set. The function
# is read back as it is now (0426 changed it last), the one place is swapped, and it is made again
# with every setting it had; a text that did not change stops the migration rather than passing
# silently (0426's way). No row changes. Text already made can lag until the next embedding run.
SQL = r"""
    DO $$
    DECLARE
        def text;
        fresh text;
    BEGIN
        def := pg_get_functiondef(
            'dmbot_entity_parts(bigint, text, text)'::regprocedure);
        fresh := replace(def, 'p.is_symmetric AS symmetric',
                         'COALESCE(r.both_ways, p.is_symmetric) AS symmetric');
        IF fresh = def OR (length(fresh) - length(def))
                          <> length('COALESCE(r.both_ways, ') + length(')') THEN
            RAISE EXCEPTION 'dmbot_entity_parts: its direction was not found exactly once';
        END IF;
        EXECUTE fresh;
    END $$;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
