"""0453_pickers_played_before

The pickers' "played with you before" mark and order come from the DM's own remembered players
(`dm_players`, 0450), across every campaign and server, not only this server (#4226). The two member
doors, the wizard's (0441) and the import page's (0445), are read back from the database as they are
(many migrations have changed them) and each anchor must be found exactly once, so a drift stops the
migration (the 0434 way). No row is changed.

The mark can only be true for someone the door already lists, and the door lists only the members of
the server being asked about, so a name never shows for a person who is not there. The import door
gains the mark (`before`) and puts remembered players ahead of the others, after an exact match for
what the DM typed.
"""

from __future__ import annotations

_WIZARD = (
    (
        "EXISTS (SELECT 1 FROM memory_entities e",
        "dmbot_dm_remembers(d.user_id)\n"
        "                           OR EXISTS (SELECT 1 FROM memory_entities e",
        1,
    ),
)
_IMPORT = (
    (
        "'userId', m.user_id::TEXT, 'name', m.name,\n",
        "'userId', m.user_id::TEXT, 'name', m.name,\n"
        "                       'before', dmbot_dm_remembers(m.user_id),\n",
        1,
    ),
    (
        "ORDER BY (lower(m.name) = lower(q)) DESC, m.rank, lower(m.name))",
        "ORDER BY (lower(m.name) = lower(q)) DESC, dmbot_dm_remembers(m.user_id) DESC,\n"
        "                                m.rank, lower(m.name))",
        1,
    ),
    (
        "ORDER BY (lower(one.name) = lower(q)) DESC, one.rank, lower(one.name)\n"
        "                  LIMIT 25",
        "ORDER BY (lower(one.name) = lower(q)) DESC, dmbot_dm_remembers(one.user_id) DESC,\n"
        "                           one.rank, lower(one.name)\n"
        "                  LIMIT 25",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _swap(name: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + name
        + "';"
        + "".join(
            f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
            for old, new, n in swaps
        )
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
    + _swap("dmbot_web_wizard_members", _WIZARD)
    + _swap("dmbot_import_members", _IMPORT)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
