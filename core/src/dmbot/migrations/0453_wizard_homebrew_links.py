"""0453_wizard_homebrew_links

Step 8 of the wizard takes links to published homebrew pages (#4211): the draft's key list takes
`homebrew_links`, and a draft with links needs the right-to-use confirmation to Finish, as one with
documents does (the IP rule: nothing is fetched or kept without it). The shape (a short list of
short strings) is checked by the website's wrapper and again by the bot at Finish, and the draft's
size limit still holds.
"""

from __future__ import annotations

# The door is read back from the database as it is and the anchor must be found exactly once, so a
# drift stops the migration instead of editing the wrong line (the 0403 and 0443 way).
_OLD = "'fallback', 'supplements', 'data_use', 'skipped')"
_NEW = "'fallback', 'supplements', 'data_use', 'skipped', 'homebrew_links')"
# `rights_shared_at = 0 AND EXISTS (... slot IN (...)) OR (rights_shared_at = 0 AND links)`: AND
# binds tighter than OR, so the two conditions read as written.
_RIGHTS_OLD = "AND x.slot IN ('lore', 'secrets', 'house', 'homebrew')) THEN"
_RIGHTS_NEW = (
    "AND x.slot IN ('lore', 'secrets', 'house', 'homebrew'))\n"
    "               OR (d.rights_shared_at = 0\n"
    "                   AND jsonb_typeof(d.answers->'homebrew_links') = 'array'\n"
    "                   AND jsonb_array_length(d.answers->'homebrew_links') > 0) THEN"
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
              AND p.proname = 'dmbot_web_wizard_set';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD
    + r"""$q$, $q$"""
    + _NEW
    + r"""$q$, 1);
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_wizard_finish';
        def := pg_temp.swap_n(def, $q$"""
    + _RIGHTS_OLD
    + r"""$q$, $q$"""
    + _RIGHTS_NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
