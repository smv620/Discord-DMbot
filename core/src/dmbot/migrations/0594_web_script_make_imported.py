"""0594_web_script_make_imported

The website's script question names AI credits for an imported session, as Discord's does (#4151,
parity with #4150 and #4005).
"""

from __future__ import annotations

# `dmbot_web_script_make_state` (0401, 0407) also says whether the session was loaded from a file
# (`imported`, a row of `session_imports`, as `TranscriptStore.is_import` reads it), so the API can
# give an imported session's cost line the credits sentence and a live session's none. A flag
# only. The function is read back from the database as it is and the anchor must be found
# exactly once, so a drift stops the migration. No row changes.
_OLD = "            'auto', EXISTS (SELECT 1 FROM theatre_runs r"
_NEW = (
    "            'imported', EXISTS (SELECT 1 FROM session_imports i\n"
    "                                WHERE i.guild_id = g AND i.session_id = sid),\n" + _OLD
)

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
              AND p.proname = 'dmbot_web_script_make_state';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD
    + r"""$q$, $q$"""
    + _NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
