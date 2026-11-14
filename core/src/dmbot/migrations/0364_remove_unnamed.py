"""0364_remove_unnamed

A DM may remove a person the story described and never named, from the website's Names page.
"""

from __future__ import annotations

# "Not named yet" people (#2803) are entries the story described but never named ("Ken's sister").
# Some are not worth keeping (a bystander the AI noticed), and the Names page had no way to drop
# them: the queue door's `remove` only found a confirmed, named entry. This lets `remove` also find
# an unnamed one (waiting or kept); the bot then turns it down like any removal, one change that
# the name card's Undo takes back. The confirmation (`needs_confirm`) and the rest of the door's
# checks are unchanged. The queue door is read back from the database as it is (many migrations
# have changed it) and the anchor must be found exactly once, so a drift stops the migration
# instead of widening the wrong rule, as 0343 and 0354 do. No row is changed.
_SWAPS = (
    (
        "OR (act IN ('name', 'name-join') AND e.unnamed\n"
        "                          AND e.status IN ('proposed', 'confirmed')))) THEN",
        "OR (act IN ('name', 'name-join', 'remove') AND e.unnamed\n"
        "                          AND e.status IN ('proposed', 'confirmed')))) THEN",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _SWAPS
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
              AND p.proname = 'dmbot_web_name_action_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
