"""0469_graph_route_state

The two graph doors carry a route's state (#4173 slice 6d part 2), so a plan or a journey is never
read as a road.
"""

from __future__ import annotations

# Supervisor's condition on part 2 (2026-11-13): the players' and the doors' handling of
# `route_state` (0467) ships before, or with, the first write of a plan or a journey. Each
# connection the viewer's graph (`dmbot_web_graph_source`) and the DM's Full View
# (`dmbot_web_relationships`) send carries it, as 0400 did `knows_how` and 0435 `both_ways`: it
# rides on a connection the door or the gate already shows, so it tells no one anything new; the
# words
# are worked out from it in `web/relationships.py` and `link_words`. The doors are read back as
# they are and each anchor must be found the exact number of times (0400's way). No row is changed.
_SOURCE_SWAPS = (
    ("'both_ways', r.both_ways,", "'both_ways', r.both_ways, 'route_state', r.route_state,", 1),
)
_RELATIONSHIPS_SWAPS = (
    ("'both_ways', r.both_ways", "'both_ways', r.both_ways, 'route_state', r.route_state", 1),
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
