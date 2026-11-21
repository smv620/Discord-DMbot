"""0584_web_thread_involve_optional

The website's "Add who's involved" can leave "Their part" empty, as the card's "(optional)" box
says (#3696, slice 2b follow-up; #4937 made the store keep an empty part, #4940).
"""

from __future__ import annotations

# `thread-involve` (0581) took `<thread id> <who's id> <part>` with a part of 1 to 60 characters,
# because the store refused an empty part. It keeps one now, so the door's shape check takes the
# part as optional (0 to 60 characters): `<thread id> <who's id>[ <part>]`. Only that one line of
# the queue door changes, and it must be found exactly once. No new action, no table, no row.
_ID = "[0-9a-f]{32}"
_OLD = f"OR (act = 'thread-involve' AND clean !~ '^{_ID} {_ID} .{{1,60}}$')"
_NEW = f"OR (act = 'thread-involve' AND clean !~ '^{_ID} {_ID}( .{{1,60}})?$')"


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
              AND p.proname = 'dmbot_web_name_action_add';
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
