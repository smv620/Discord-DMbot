"""0462_assumed_links

An "assumed" mark on connections and on waiting connections (#4173 slice 3).
"""

from __future__ import annotations

# Supervisor's rulings on #4173 (2026-11-11 and 2026-11-13): a place's parent that the lore reader
# assumed from where the story was (`memory/story_chain.py`), never stated, is an ordinary
# `located_in` with this mark. It waits like any read connection (`backstory_links.assumed`), is
# never asked about, and is kept by itself once both its places are kept. The kept connection keeps
# the mark ("(assumed)" on the DM's card) until the DM says otherwise: ✅ That's right, or a stated
# parent, which replaces it. It is held back from the players until then.
#
# Both columns are new with a default of false, so no existing row changes and no row-level
# security needs opening.
#
# An assumed waiting link is never one of the DM's questions, on Discord (`BackstoryLinks` leaves it
# out) or on the website: the two doors that read `backstory_links` (`dmbot_web_waiting`, its list
# and its count, and `dmbot_web_name_action_add`, which checks a Keep or Skip names a waiting row)
# read it through `NOT assumed`. Each door is read back from the database as it is, and its anchor
# must be found exactly twice, so a drift stops the migration (as 0403 and 0436 do). The website's
# half (the mark for the DM, nothing for players while assumed) is its own issue, and every player
# door already hides a held-back connection.
_OLD = "FROM backstory_links b"
_NEW = "FROM (SELECT * FROM backstory_links WHERE NOT assumed) b"
_DOORS = ("dmbot_web_waiting", "dmbot_web_name_action_add")


def _swap(door: str) -> str:
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
        + "';\n        def := pg_temp.swap_n(def, $q$"
        + _OLD
        + "$q$, $q$"
        + _NEW
        + r"""$q$, 2);
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = r"""
    ALTER TABLE memory_relations ADD COLUMN assumed BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE backstory_links ADD COLUMN assumed BOOLEAN NOT NULL DEFAULT false;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
    """ + "".join(_swap(d) for d in _DOORS)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
