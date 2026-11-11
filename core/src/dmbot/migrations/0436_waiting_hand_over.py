"""0436_waiting_hand_over

The waiting list says when a connection means an item changed hands (#4122, the web half of #4115):
"Belleros owns the Frost Brand (Claude sold it)".
"""

from __future__ import annotations

# Migration 0423 gave a waiting connection `hand_over` (sold, gave, traded, stolen, lost) and
# `hand_over_from` (the entry that had the item before). This sends both beside each waiting row,
# with that entry's name (`handOverName`), so the page can show the bracket and say what Keep will
# do. The previous holder is found the way the bot finds it (`BackstoryLinks._end`, MERGE_HOPS = 6
# and the rules after it): followed through merges, and a hand-over is dropped (a plain
# connection) when that entry is gone, turned down, one of the connection's own two ends, or the
# predicate is not owns or carries. The two must agree, or the page would offer a Keep the bot
# refuses. The names are ones the DM already sees; DM only, like the rest of the door. The door is
# read back from the database as it is (many migrations have changed it) and the anchor must be
# found exactly once, so a drift stops the migration instead of editing the wrong line (the 0403
# way). No row is changed.
_OLD = "'truth', l.truth, 'upload', l.upload,"
_HOLDER = "dmbot_hand_holder(g, c, l.hand_over_from, l.subject_id, l.object_id, l.predicate)"
_NEW = (
    "'truth', l.truth, 'upload', l.upload,\n"
    f"                    'handOverName', {_HOLDER} ->> 'name',\n"
    f"                    'handOver', CASE WHEN {_HOLDER} IS NULL THEN '' ELSE l.hand_over END,\n"
    "                    'handOverFrom', CASE WHEN "
    f"{_HOLDER} IS NULL THEN '' ELSE {_HOLDER} ->> 'id' END,"
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
    -- The entry that stands for the previous holder now, as {id, name}, or NULL when the hand-over
    -- is dropped (see above). Internal: only the door below calls it.
    CREATE FUNCTION dmbot_hand_holder(
        g BIGINT, c TEXT, frm TEXT, subj TEXT, obj TEXT, pred TEXT) RETURNS JSONB
    LANGUAGE sql STABLE AS $fn$
        WITH RECURSIVE chain(id, name, status, merged_into, hop) AS (
            SELECT e.id, e.name, e.status, e.merged_into, 0 FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = frm AND frm <> ''
            UNION ALL
            SELECT e.id, e.name, e.status, e.merged_into, ch.hop + 1
            FROM chain ch JOIN memory_entities e
                 ON e.guild_id = g AND e.campaign_id = c AND e.id = ch.merged_into
            WHERE ch.status = 'merged' AND ch.hop < 5
        )
        SELECT jsonb_build_object('id', h.id, 'name', h.name) FROM chain h
        WHERE h.status NOT IN ('merged', 'rejected') AND h.id NOT IN (subj, obj)
          AND pred IN ('owns', 'carries')
        ORDER BY h.hop LIMIT 1
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_hand_holder(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_hand_holder(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
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
