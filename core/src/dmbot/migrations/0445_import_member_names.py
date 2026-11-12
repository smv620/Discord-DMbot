"""0445_import_member_names

The import page's member search shows a Discord name, never "Player 1520" (#4225).
"""

from __future__ import annotations

# A person DMbot knows only by a Discord id is named `Player <last 4 digits>` (`memory/store.py`,
# since 0058), and the member search ranked a campaign person's name first, so that placeholder won
# over the display name in `member_directory`. Now a placeholder name counts as no name: the
# directory's name (or the website's) wins, and when there is none the label is "Unknown member
# (…1520)", so nobody mistakes it for a character. The search also sends the username, so the page
# can show "Eric (eric_92)". A person who has not agreed is still listed (the page marks them), as
# before. The door is read back from the database as it is (many migrations have changed it) and
# each anchor must be found exactly once, so a drift stops the migration (the 0434 way). No row is
# changed. "Player 3 (no Discord)" is a person the DM added who is not in Discord (a negative id):
# a name the DM can recognise, so it is left alone (Supervisor, #4228).
_PLACEHOLDER = "e.name ~ '^Player [0-9]+$'"
_SWAPS = (
    (
        "SELECT e.played_by AS user_id, e.name, 0 AS rank, e.name AS hay",
        "SELECT e.played_by AS user_id,\n"
        f"                                 CASE WHEN {_PLACEHOLDER}\n"
        "                                      THEN 'Unknown member (…'\n"
        "                                           || right(e.played_by::TEXT, 4) || ')'\n"
        "                                      ELSE e.name END,\n"
        f"                                 CASE WHEN {_PLACEHOLDER} THEN 3 ELSE 0 END AS rank,\n"
        "                                 e.name AS hay",
        1,
    ),
    (
        "OR n.rank = 0",
        "OR n.rank IN (0, 3)",
        1,
    ),
    (
        "'userId', m.user_id::TEXT, 'name', m.name,",
        "'userId', m.user_id::TEXT, 'name', m.name,\n"
        "                       'username', (SELECT NULLIF(d.username, '')\n"
        "                                    FROM member_directory d\n"
        "                                    WHERE d.guild_id = g AND d.user_id = m.user_id),",
        1,
    ),
)


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
              AND p.proname = 'dmbot_import_members';"""
    + "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in _SWAPS
    )
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
