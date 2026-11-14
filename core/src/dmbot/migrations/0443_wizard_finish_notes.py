"""0443_wizard_finish_notes

A wizard finish run can end well and still have something to say about several characters at once
(#4205): "2 sheets were private, 1 was slow". The row's `error` keeps one word, so the counts of
each word go in `notes`, and the page's finish-state door sends them.
"""

from __future__ import annotations

# `notes` is words and counts only, like the rest of the row ("sheet_private:2 sheet_slow:1"): a
# word of at most 24 letters or underscores, a colon, a count of at most 3 digits, up to 8 pairs,
# one space between them. The check keeps a name, a link or a sentence out of it. The door is read
# back from the database as it is and the anchor must be found exactly once, so a drift stops the
# migration instead of editing the wrong line (the 0403 way). Existing rows get '' (no notes).
_OLD = "'error', r.error,"
_NEW = "'error', r.error, 'notes', r.notes,"

SQL = (
    r"""
    ALTER TABLE wizard_finishes ADD COLUMN notes TEXT NOT NULL DEFAULT ''
        CHECK (notes ~ '^([a-z_]{1,24}:[0-9]{1,3}( [a-z_]{1,24}:[0-9]{1,3}){0,7})?$');
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
              AND p.proname = 'dmbot_web_wizard_finish_state';
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
