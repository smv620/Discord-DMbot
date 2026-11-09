"""0400_graph_knows_how

The Story Graph says whether two people have met (#3882, the web half of #3881).
"""

from __future__ import annotations

# Each connection the two graph doors send carries `knows_how` ("met", "aware" or "", 0397): the
# viewer's graph (`dmbot_web_graph_source`, what the gate filters for each viewer) and the DM's Full
# View (`dmbot_web_relationships`). It rides on the connection itself, so only someone the door or
# the gate already shows that connection to is told it: a secret meeting stays as hidden as the
# connection that says it (and the store never lets one move a public line up, #3881). The doors
# are read back from the database as they are and each anchor must be found the exact number of
# times, so a drift stops the migration instead of changing the wrong line (as 0376 does). No row
# is changed.
_SOURCE_SWAPS = (("'via', r.via_alias_id,", "'via', r.via_alias_id, 'knows_how', r.knows_how,", 1),)
_RELATIONSHIPS_SWAPS = (
    ("'directional', r.directional", "'directional', r.directional, 'knows_how', r.knows_how", 1),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _swap(function: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    """One DO block: `function` read back as it is, each swap made, and made again."""
    steps = "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + function
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
"""
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
"""
    + _swap("dmbot_web_graph_source", _SOURCE_SWAPS)
    + _swap("dmbot_web_relationships", _RELATIONSHIPS_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
