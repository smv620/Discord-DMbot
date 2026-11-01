"""0275_web_decided_note

DMbot's check on the website (#2859).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _swaps,
)

# DMbot's check of a pronoun on the website (#2859; the `decided` note of #2790, stored since 0267):
# a DM sees the same note Discord shows on a waiting connection, a "Which one?" question and a
# connection's card. The two doors carry the stored words (a fixed vocabulary, never a name or any
# text) to a DM only; the website's Python turns them into plain words with the one function
# Discord uses (`who_resolve.checked_line`), so there is no second copy. The doors are read back
# from the database as they are and widened, each anchor found exactly once (a drift stops the
# migration), as 0266 and 0270 do. No table changes, no rows change.
_DECIDED_WAITING_SWAPS = (
    ("'subjectId', l.subject_id,", "'subjectId', l.subject_id, 'decided', l.decided,"),
    (
        "'otherIsSubject', w.other_is_subject,",
        "'otherIsSubject', w.other_is_subject, 'decided', w.decided,",
    ),
)

_DECIDED_EDGE_SWAPS = (
    (
        "'at', r.created_at,",
        "'at', r.created_at,\n"
        "                    'decided', CASE WHEN is_dm AND sees_all THEN r.decided END,",
    ),
)

WEB_DECIDED_NOTE = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_waiting';
"""
    + _swaps("def", _DECIDED_WAITING_SWAPS)
    + r"""
        EXECUTE def;

        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_relationships';
"""
    + _swaps("def", _DECIDED_EDGE_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
"""
)

SQL = WEB_DECIDED_NOTE
