"""0523_who_common_text

The website's "Who knows this?" takes a fourth answer, 🌍 Common knowledge (#4559, #4537 slice 1b,
web half).
"""

from __future__ import annotations

# 0314 gave the queue door two actions, `link-who` (a connection) and `alias-who` (a secret other
# name), whose `text` is exactly `dm`, `everyone`, or `some` and then one to fifty character ids.
# The bot applies each with the call Discord's buttons use (`link_edits.set_who_knows`,
# `name_edits.set_who_knows`), which since #4558 also takes `common`: every character knows it,
# NPCs and later joiners too; the item is shown to the players first if it is hidden, and the
# answer is refused (`cannot_show`) while an end of the connection, or the name's entry, is kept
# from them.
# This lets the door's shape check take `common` (no characters) as a fourth word; the bot checks
# the rest again. The door is read back from the database as it is (0314 and 0315 changed it) and
# the anchor must be found exactly once, so a drift stops the migration. No row is changed.
_OLD = "AND clean !~ '^(dm|everyone|some( [0-9a-f]{32}){1,50})$')"
_NEW = "AND clean !~ '^(dm|everyone|common|some( [0-9a-f]{32}){1,50})$')"


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
