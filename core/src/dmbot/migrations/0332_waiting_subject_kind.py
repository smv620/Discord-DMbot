"""0332_waiting_subject_kind

The kind of a waiting connection's subject, so the website's place words fit it (#3368, #3363).
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps


# A place tie reads by what its subject is: a character "lives in" or "was last seen in" a town,
# a town "is in" a region (#3363). The relationship door sends each name's kind; the waiting list
# did not, so a character's waiting backstory still said "is in". Each backstory row now carries
# `subjectKind`, and each "Which one?" row `otherKind` (the kind of the name it already shows, the
# subject only when `otherIsSubject` says so: the other end is a list the DM has yet to choose
# from, and its kind is not known). The kind is the same one the graph door sends: a character's
# role (player character, NPC, god as `deity`), else the entry's own type. A word, never a name or
# any text, and the door is the DM's alone, as before. The door is read back from the database as
# it is and widened, each anchor found exactly once (a drift stops the migration), as 0275 does.
# No table changes, no rows change.
def _kind_of(alias: str) -> str:
    """The kind word for an entry (the graph door's, in `_shared.py`): a character's role, else
    its own type. Built for the alias it is read through, so nothing is rewritten by accident."""
    return (
        f"CASE WHEN {alias}.type = 'character' THEN CASE {alias}.role "
        "WHEN 'player_character' THEN 'player_character' WHEN 'npc' THEN 'npc' "
        f"WHEN 'god' THEN 'deity' ELSE 'character' END ELSE {alias}.type END"
    )


_KIND_OF_S = _kind_of("s")
_KIND_OF_O = _kind_of("o")

# (The anchors stop before `decided`: the door's note read is 0275's alone, and a guard test keeps
# every other migration from selecting it.)
_WAITING_KIND_SWAPS = (
    (
        "'subjectId', l.subject_id,",
        f"'subjectId', l.subject_id, 'subjectKind', {_KIND_OF_S},",
    ),
    (
        "'otherIsSubject', w.other_is_subject,",
        f"'otherIsSubject', w.other_is_subject, 'otherKind', {_KIND_OF_O},",
    ),
)

WAITING_SUBJECT_KIND = (
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
    + _swaps("def", _WAITING_KIND_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = WAITING_SUBJECT_KIND
