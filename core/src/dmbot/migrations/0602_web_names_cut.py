"""0602_web_names_cut

The lore and import doors also send how many names a limit left out (#5072, web half of #5055 A).
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# 0599 keeps two counts, never a name: `lore_uploads.names_cut` (names past one document's 200) and
# `session_imports.scan_cut` (names the name scan left out past its 10). The website says so in the
# same words as Discord ("never silent"), so the two read doors send them. Both are rebuilt from
# their stored definitions by guarded replacement (a drift stops the migration), as 0351 did, so
# the search path and privileges they have now stay. No table or row changes.
_IMPORT_SWAPS = (
    (
        "'wordsDone', i.words_done, 'callsMade', i.calls_made,",
        "'wordsDone', i.words_done, 'callsMade', i.calls_made,\n"
        "                       'namesCut', i.scan_cut,",
    ),
)
_LORE_SWAPS = (
    (
        "'wordsDone', r.words_done, 'callsMade', r.calls_made,",
        "'wordsDone', r.words_done, 'callsMade', r.calls_made,\n"
        "            'namesCut', r.names_cut,",
    ),
)

WEB_NAMES_CUT = (
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
              AND p.proname = 'dmbot_import_progress';
"""
    + _swaps("def", _IMPORT_SWAPS)
    + r"""
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_lore_read';
"""
    + _swaps("def", _LORE_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = WEB_NAMES_CUT

ISOLATED_TABLES: tuple[str, ...] = ()
