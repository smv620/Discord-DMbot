"""0617_web_undo_a_yes

The website's Undo of a yes that made rules (#5144, the website half of #5133 slice 3c-2b).
"""

from __future__ import annotations

# A yes on a name with a found row (`recaps/name_answers._confirm_found`) now stays as a `done` row
# with its batch, as a split or group does, so the page can take it back. The door's check that an
# `undo` points at a finished answer of this name listed the three kinds that stayed (0335);
# `confirm` is the fourth. The door is read back from the database as it is and the one anchor
# must be found exactly once, so a door that has drifted stops the migration instead of being
# rewritten from old text. No table changes: `undo`, `batch`, `note` and `undo_name` are 0335's.
_OLD = "AND u.action IN ('split', 'join_group', 'make_group')) THEN"
_NEW = "AND u.action IN ('split', 'join_group', 'make_group', 'confirm')) THEN"


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
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
              AND p.proname = 'dmbot_name_answer_add';
        def := pg_temp.swap_n(def, """
    + _dollar(_OLD)
    + ", "
    + _dollar(_NEW)
    + r""", 1);
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
