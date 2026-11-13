"""0476_place_read_dm_only

The All names door sends a place's size or kind that DMbot only read to the DM only (#4356, after
#4286).
"""

from __future__ import annotations

# #4286 hid a size or kind DMbot only read from a player's Discord card: it is the DM's to check
# (as #4256 does for land, climate and ruler, and 0459 for a guessed setting). The website's All
# names door (`dmbot_web_all_names`) still sent `placeLevel`, `placeLevelFrom`, `form` and
# `formFrom` to everyone who can see the name, so a player's browser held a read value, marked
# 'read': a sign of what the DM's notes said, though the page never showed it. Each is now sent to
# a player only once the DM has said it (`*_from = 'dm'`), and to the DM always, as a guessed
# `setting` is (0459). The door is read back from the database as it is and each anchor (0437's and 0459's lines)
# must be found exactly once, so a drift stops the migration (0459's way). No row changes.
_READ_LEVEL = "is_dm OR p.place_level_from = 'dm'"
_READ_FORM = "is_dm OR p.form_from = 'dm'"
_ALL_NAMES_SWAPS = (
    (
        "                    'placeLevel', p.place_level, 'placeLevelFrom', p.place_level_from,\n",
        f"                    'placeLevel', CASE WHEN {_READ_LEVEL} THEN p.place_level END,\n"
        f"                    'placeLevelFrom', CASE WHEN {_READ_LEVEL} THEN p.place_level_from"
        " END,\n",
        1,
    ),
    (
        "                    'form', p.form, 'formFrom', p.form_from,\n",
        f"                    'form', CASE WHEN {_READ_FORM} THEN p.form END,\n"
        f"                    'formFrom', CASE WHEN {_READ_FORM} THEN p.form_from END,\n",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _rebuild(door: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
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
        + door
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
    """ + _rebuild("dmbot_web_all_names", _ALL_NAMES_SWAPS)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
