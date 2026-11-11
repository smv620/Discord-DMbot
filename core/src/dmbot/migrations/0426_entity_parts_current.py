"""0426_entity_parts_current

An entry's search text names only the connections that hold now (#4115 slice 3).
"""

from __future__ import annotations

# `dmbot_entity_parts` (0253, guarded again by 0264) writes "Connections: owns the Frost Brand…"
# into each entry's text for the bot's search numbers, the Names page's reranker and the sidebar's
# leads, which reach the players' answers. It never looked at whether a connection had ended, and
# from #4115 an `owns` or a `carries` ends when an item changes hands: Claude who sold the sword
# must not still "own" it in the text. So an ended connection (`to_session_at` set) is left out,
# whatever its kind: the text says what the entry is now, and the history stays where it is shown
# as history. The function is read back as it is now (0264 changed it), the one filter on what is
# kept is widened, and it is made again with every setting it had; a text that did not change
# stops the migration rather than passing silently. Text already made can lag until the next
# embedding run, as for an entry that becomes hidden.
SQL = r"""
    DO $$
    DECLARE
        def text;
        fresh text;
    BEGIN
        def := pg_get_functiondef(
            'dmbot_entity_parts(bigint, text, text)'::regprocedure);
        fresh := replace(def, 'AND NOT r.secret AND NOT r.unrevealed',
                         'AND NOT r.secret AND NOT r.unrevealed AND r.to_session_at IS NULL');
        IF fresh = def OR (length(fresh) - length(def))
                          <> length(' AND r.to_session_at IS NULL') THEN
            RAISE EXCEPTION 'dmbot_entity_parts: its connection filter was not found exactly once';
        END IF;
        EXECUTE fresh;
    END $$;
"""
