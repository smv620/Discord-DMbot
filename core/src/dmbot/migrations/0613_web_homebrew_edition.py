"""0613_web_homebrew_edition

The website's homebrew door also says which edition a shared book's entry is for (#5156).
"""

from __future__ import annotations

# The website's rule lookup ranks a shared book's entry next to the free rules as Discord's does
# (`rules/book_rank.py`: with the target ruleset, with the fallback, or between, by
# `book_edition`). The door already sends `legacy` (a 2014 book while the target is newer) but not
# the edition itself, which tells a 2024 book from "other" (a supplement). The edition is a label
# (2024, 2014 or other), never a word of the entry. The door is read back from the database and
# swapped by guarded replacement (the anchor must be found exactly once, so a drift stops the
# migration), which keeps its search path and privileges. No row changes.
_OLD = "'fromBook', h.from_book, 'enabled', h.enabled,"
_NEW = "'fromBook', h.from_book, 'bookEdition', h.book_edition, 'enabled', h.enabled,"


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
              AND p.proname = 'dmbot_web_homebrew';
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
