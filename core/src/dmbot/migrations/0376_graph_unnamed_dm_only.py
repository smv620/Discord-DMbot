"""0376_graph_unnamed_dm_only

People the story never named on the Story Graph are the DM's alone (#3732): the graph door and its
overview give them, and the lines to them, only to a DM's Full View, never to a player's peek.
"""

from __future__ import annotations

# `dmbot_web_relationships` serves the Full View: a DM's, and a player's peek behind the DM screen.
# Its names were every confirmed entry but people, so a peek was handed a confirmed unnamed person
# ("Ken's sister (name unknown)") and the lines to them, against 0264's rule that an unnamed person
# never reaches a player. Now only a DM gets them; a line needs both ends in the list, so their
# lines go with them. For the DM, each unnamed name also carries its parts (`unnamed`,
# `unnamedWhat`, `unnamedAnchor`, as the All names door sends them since 0266), so the API builds
# the label from the anchor's present name (`web/all_names._label_unnamed`), never the one stored
# when the person was made. Waiting unnamed people stay off the map like every waiting entry; a DM
# is told how many there are (`waitingUnnamed`, #3732 ruling), so the page can point to Names → Not
# named yet. The door is read back from the database as it is (many migrations have changed it)
# and each anchor must be found the exact number of times, so a drift stops the migration instead
# of changing the wrong line, as 0369 and 0372 do. No row is changed.
_WAITING = (
    "'waitingUnnamed', CASE WHEN is_dm AND sees_all THEN (\n"
    "                SELECT count(*)::int FROM memory_entities wu\n"
    "                WHERE wu.guild_id = g AND wu.campaign_id = c\n"
    "                  AND wu.unnamed AND wu.status = 'proposed') END,"
)
# The names on the map: an unnamed person only for a DM (a peek is `sees_all AND NOT is_dm`). Both
# doors list their names with this same line.
_NAMES_GUARD = (
    "AND e.status = 'confirmed' AND e.type <> 'person');",
    "AND e.status = 'confirmed' AND e.type <> 'person'\n"
    "              AND (is_dm OR NOT e.unnamed));",
    1,
)
_SWAPS = (
    _NAMES_GUARD,
    # the parts of an unnamed name, for a DM only (the API builds the label from them)
    (
        "'id', e.id, 'name', e.name,",
        "'id', e.id, 'name', e.name,\n"
        "                    'unnamed', is_dm AND e.unnamed,\n"
        "                    'unnamedWhat',\n"
        "                        CASE WHEN is_dm AND e.unnamed THEN e.unnamed_what END,\n"
        "                    'unnamedAnchor', CASE WHEN is_dm AND e.unnamed THEN (\n"
        "                        SELECT jsonb_build_object(\n"
        "                            'name', x.name, 'what', x.unnamed_what,\n"
        "                            'anchorName', y.name)\n"
        "                        FROM memory_entities x\n"
        "                        LEFT JOIN memory_entities y ON y.guild_id = x.guild_id\n"
        "                             AND y.campaign_id = x.campaign_id\n"
        "                             AND y.id = x.unnamed_anchor\n"
        "                        WHERE x.guild_id = g AND x.campaign_id = c\n"
        "                          AND x.id = e.unnamed_anchor) END,",
        1,
    ),
    # how many are waiting, on an empty map and on a drawn one
    (
        "'names', 0, 'nodes', '[]'::jsonb, 'edges', '[]'::jsonb,",
        "'names', 0, 'nodes', '[]'::jsonb, 'edges', '[]'::jsonb,\n"
        "                                      " + _WAITING,
        1,
    ),
    (
        "'truncated', total > GREATEST(cap, 1),",
        "'truncated', total > GREATEST(cap, 1),\n            " + _WAITING,
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


# The overview door (the map's whole-campaign summary) lists the same names the same way and is
# peeked at the same way, so its list takes the same guard.
_OVERVIEW_SWAPS = (_NAMES_GUARD,)


def _steps(swaps: tuple[tuple[str, str, int], ...]) -> str:
    return "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )


def _swap(function: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    """One DO block: `function` read back as it is, each swap made, and made again."""
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
        + _steps(swaps)
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
    + _swap("dmbot_web_relationships", _SWAPS)
    + _swap("dmbot_web_graph_overview", _OVERVIEW_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
