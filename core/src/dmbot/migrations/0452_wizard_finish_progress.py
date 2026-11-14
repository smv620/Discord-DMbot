"""0452_wizard_finish_progress

Finer counts on the wizard's finish row while the lore is read (#4213): which pass the reader is
in, how far through it, and what it has found so far, so the page can show motion that is real.
Numbers and one stage word only, never a word of a document (CLAUDE.md, "Every wait shows that
DMbot is still working"); the bot copies them from the lore upload's own row at most once a second.
"""

from __future__ import annotations

# The door is read back from the database as it is and the anchor must be found exactly once, so a
# drift stops the migration instead of editing the wrong line (the 0403 and 0443 way).
_OLD = "'error', r.error,"
_NEW = (
    "'error', r.error, 'pass', r.pass_word, 'passDone', r.pass_done, 'passTotal', r.pass_total,"
    " 'foundNames', r.found_names, 'foundLinks', r.found_links,"
)

SQL = (
    r"""
    ALTER TABLE wizard_finishes
        ADD COLUMN pass_word TEXT NOT NULL DEFAULT '' CHECK (pass_word ~ '^[a-z_]{0,40}$'),
        ADD COLUMN pass_done INT NOT NULL DEFAULT 0 CHECK (pass_done >= 0),
        ADD COLUMN pass_total INT NOT NULL DEFAULT 0 CHECK (pass_total >= 0),
        ADD COLUMN found_names INT NOT NULL DEFAULT 0 CHECK (found_names >= 0),
        ADD COLUMN found_links INT NOT NULL DEFAULT 0 CHECK (found_links >= 0);
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
