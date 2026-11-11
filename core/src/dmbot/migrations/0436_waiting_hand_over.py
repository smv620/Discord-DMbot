"""0436_waiting_hand_over

The waiting list says when a connection means an item changed hands (#4122, the web half of #4115):
"Belleros owns the Frost Brand (Claude sold it)".
"""

from __future__ import annotations

# Migration 0423 gave a waiting connection `hand_over` (sold, gave, traded, stolen, lost) and
# `hand_over_from` (the entry that had the item before). This sends both beside each waiting row,
# with that entry's name (`handOverName`), so the page can show the bracket and say what Keep will
# do. The door sends a hand-over only while the one it came from is still a live entry (followed
# through one merge, as the queue is read by the bot): when it is gone, the row is a plain
# connection and says nothing of a hand-over, which is also what keeping it does. The names are
# ones the DM already sees on the page; DM only, like the rest of the door. The door is read back
# from the database as it is (many migrations have changed it) and the anchor must be found
# exactly once, so a drift stops the migration instead of editing the wrong line (the 0403 way).
# No row is changed.
_OLD = "'truth', l.truth, 'upload', l.upload,"
_FROM = (
    "(SELECT n.name FROM memory_entities p"
    " JOIN memory_entities n ON n.guild_id = p.guild_id AND n.campaign_id = p.campaign_id"
    " AND n.id = COALESCE(p.merged_into, p.id)"
    " WHERE p.guild_id = g AND p.campaign_id = c AND p.id = l.hand_over_from"
    " AND n.status IN ('confirmed', 'proposed'))"
)
_NEW = (
    "'truth', l.truth, 'upload', l.upload,\n"
    f"                    'handOverName', {_FROM},\n"
    f"                    'handOver', CASE WHEN {_FROM} IS NULL THEN '' ELSE l.hand_over END,\n"
    "                    'handOverFrom', CASE WHEN "
    f"{_FROM} IS NULL THEN '' ELSE l.hand_over_from END,"
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
              AND p.proname = 'dmbot_web_waiting';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD
    + r"""$q$, $q$"""
    + _NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
