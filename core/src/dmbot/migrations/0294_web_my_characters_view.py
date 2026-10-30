"""0294_web_my_characters_view

A signed-in player sees what their own characters know on the Story Graph and its overview, under
the names those characters know (#3037, slice B; STORY_MEMORY §9b items 6 and 8).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _swaps,
)

# The players' view of the graph used to be the table's: what the flags show everyone. A player
# also knows things their own characters were told (a secret connection, an entry under a secret
# other name), and after "the guard at the gate" is joined with "Ken" the character who knew only
# the guard knows the entry only as that. So, for a player who is not seeing everything (a DM's full
# view and a peek behind the DM screen are untouched), the two doors also list:
#   - an entry one of THEIR OWN characters knows (a row in `memory_knowers` for the entry, or for a
#     secret other name of it), named as that character knows it: the main name only if a character
#     of theirs knows the entry under its own name, else the first other name they learned;
#   - a connection one of their characters knows, when both its ends are shown to them. A
#     connection that touches such an entry is shown only if a character of theirs knows it: the
#     table-wide connections of a hidden entry never come with it.
# Only the signed-in player's own characters count (`played_by`), never another player's, and
# nothing reaches an anonymous reader (a shared link has no signed-in player). An entry the flags
# already show keeps the name everyone sees. The helpers are internal: they are not given to the
# website's role, and the doors (SECURITY DEFINER) are the only callers (they check the caller's
# server first; the helpers do not, so they must stay unreachable from outside). The SQL reads only
# what a character's own rows grant (an entry row, or a row on a secret other name or a
# connection); `gate.visible_to` also honours groups, self-knowledge and entries a known statement
# names, so the website may show a player a little less than the Python readers do, never more.
# A connection's free-text `detail` is the DM's own note and is sent as written.
# The doors are read back from the database as they are and widened, each anchor found exactly
# once (a drift stops the migration), as 0281 does. No table changes, no rows change.
_COMMENT = "        -- The connections the caller may see, worked out once"
_COMMENT += " (every use below reads this list)."
_SHARED_SWAPS = (
    (
        _COMMENT,
        "        flag_vis := vis;\n"
        "        IF NOT sees_all THEN\n"
        "            SELECT COALESCE(jsonb_object_agg(m.entity_id, m.shown_name), '{}'::jsonb)\n"
        "            INTO alt_names\n"
        "            FROM dmbot_my_known_entries(g, c) m WHERE NOT (m.entity_id = ANY (vis));\n"
        "            vis := ARRAY(SELECT DISTINCT x FROM unnest(\n"
        "                vis || ARRAY(SELECT m.entity_id\n"
        "                             FROM dmbot_my_known_entries(g, c) m)) x);\n"
        "            my_rels := ARRAY(SELECT r FROM dmbot_my_known_relations(g, c) r);\n"
        "        END IF;\n" + _COMMENT,
    ),
    (
        "AND (sees_all OR (EXISTS (SELECT 1 FROM memory_beliefs pb JOIN",
        "AND (sees_all OR ((r.id = ANY (my_rels) OR (r.subject_id = ANY (flag_vis)\n"
        "                  AND r.object_id = ANY (flag_vis)\n"
        "                  AND EXISTS (SELECT 1 FROM memory_beliefs pb JOIN",
    ),
    (
        "AND x.relation_id = pb.relation_id)) AND r.to_session_at IS NULL",
        "AND x.relation_id = pb.relation_id)))) AND r.to_session_at IS NULL",
    ),
)

_DECLARE = (
    "        flag_vis TEXT[];\n"
    "        alt_names JSONB := '{}'::jsonb;\n"
    "        my_rels TEXT[] := '{}';\n"
)
_NODE = "COALESCE(alt_names->>e.id, e.name)"  # the name a node is shown by

# The node's `revealed` flag says whether the DM keeps the entry from the players; a player is never
# told that (every entry they are shown is "revealed" to them), so it is true unless the caller sees
# everything (the DM's full view and a peek, as before).
_REVEALED = (
    "'revealed', NOT (e.hidden_from_players OR e.unnamed OR e.audience_limited)",
    "'revealed', NOT sees_all OR NOT (e.hidden_from_players OR e.unnamed OR e.audience_limited)",
)

_RELATIONSHIPS_SWAPS = (
    ("        kept TEXT[];\n    BEGIN", "        kept TEXT[];\n" + _DECLARE + "    BEGIN"),
    *_SHARED_SWAPS,
    # the names the walk orders by, and the node's name
    (
        "SELECT e.id, e.name,\n                       (SELECT count(*)",
        "SELECT e.id, " + _NODE + " AS name,\n                       (SELECT count(*)",
    ),
    ("ORDER BY x.d, lower(e.name), x.id)", "ORDER BY x.d, lower(" + _NODE + "), x.id)"),
    (
        "'id', e.id, 'name', e.name,\n                    'revealed'",
        "'id', e.id, 'name', " + _NODE + ",\n                    'revealed'",
    ),
    _REVEALED,
)

_OVERVIEW_SWAPS = (
    ("        rel_ids TEXT[];\n    BEGIN", "        rel_ids TEXT[];\n" + _DECLARE + "    BEGIN"),
    *_SHARED_SWAPS,
    (
        "SELECT e.id, lower(e.name) AS n, jsonb_build_object(\n"
        "                        'id', e.id, 'name', e.name,",
        "SELECT e.id, lower(" + _NODE + ") AS n, jsonb_build_object(\n"
        "                        'id', e.id, 'name', " + _NODE + ",",
    ),
    (
        "ORDER BY lower(e.name), e.id LIMIT GREATEST(cap_names, 1)) x)",
        "ORDER BY lower(" + _NODE + "), e.id LIMIT GREATEST(cap_names, 1)) x)",
    ),
    _REVEALED,
)

WEB_MY_CHARACTERS_VIEW = (
    r"""
    -- The signed-in player's own confirmed characters' points of view. Nobody else's: `played_by`
    -- is the Discord id of the signed-in person; with no signed-in person (a shared link) it is
    -- empty.
    CREATE FUNCTION dmbot_my_perspectives(g BIGINT, c TEXT) RETURNS SETOF TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT p.id FROM memory_perspectives p
        JOIN memory_entities pc ON pc.guild_id = p.guild_id AND pc.campaign_id = p.campaign_id
                               AND pc.id = p.entity_id
        WHERE p.guild_id = g AND p.campaign_id = c AND p.kind = 'pc'
          AND dmbot_current_user() IS NOT NULL
          AND pc.type = 'character' AND pc.role = 'player_character'
          AND pc.status = 'confirmed' AND pc.merged_into IS NULL
          AND pc.played_by = dmbot_current_user()
    $fn$;

    -- The entries those characters know, and the name each is shown by (`gate.display_name`): the
    -- main name only if one of the characters knows the entry under its own name (a row for the
    -- entry, or for the name that is its own), else the first other name they learned. An entry
    -- none of them may name is not listed. Confirmed entries only; the DM's kinds of note
    -- (threads, quests...) are never listed, as the table's view leaves them out.
    CREATE FUNCTION dmbot_my_known_entries(g BIGINT, c TEXT)
    RETURNS TABLE (entity_id TEXT, shown_name TEXT)
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        WITH own AS (
            SELECT k.about_entity_id AS eid FROM memory_knowers k
            WHERE k.guild_id = g AND k.campaign_id = c AND k.about_entity_id IS NOT NULL
              AND k.confidence > 0 AND k.perspective_id IN (SELECT dmbot_my_perspectives(g, c))
            UNION
            SELECT a.entity_id FROM memory_knowers k
            JOIN memory_aliases a ON a.guild_id = k.guild_id AND a.campaign_id = k.campaign_id
                                 AND a.id = k.alias_id
            JOIN memory_entities en ON en.guild_id = a.guild_id AND en.campaign_id = a.campaign_id
                                   AND en.id = a.entity_id
            WHERE k.guild_id = g AND k.campaign_id = c AND k.confidence > 0
              AND k.perspective_id IN (SELECT dmbot_my_perspectives(g, c))
              AND a.status = 'confirmed'
              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                = regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g')
        ), learned AS (
            SELECT a.entity_id AS eid, a.text, min(k.created_at) AS at, a.id
            FROM memory_knowers k
            JOIN memory_aliases a ON a.guild_id = k.guild_id AND a.campaign_id = k.campaign_id
                                 AND a.id = k.alias_id
            JOIN memory_entities en ON en.guild_id = a.guild_id AND en.campaign_id = a.campaign_id
                                   AND en.id = a.entity_id
            WHERE k.guild_id = g AND k.campaign_id = c AND k.confidence > 0
              AND k.perspective_id IN (SELECT dmbot_my_perspectives(g, c))
              AND a.status = 'confirmed' AND a.secret
              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
               <> regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g')
            GROUP BY a.entity_id, a.text, a.id
        ), first_other AS (
            SELECT DISTINCT ON (eid) eid, text FROM learned ORDER BY eid, at, id
        )
        SELECT e.id,
               CASE WHEN e.id IN (SELECT eid FROM own) THEN e.name ELSE f.text END
        FROM memory_entities e
        LEFT JOIN first_other f ON f.eid = e.id
        WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'confirmed'
          AND e.merged_into IS NULL AND e.type <> 'person'
          AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal', 'promise', 'deed')
          AND (e.id IN (SELECT eid FROM own) OR f.eid IS NOT NULL)
    $fn$;

    -- The connections those characters know.
    CREATE FUNCTION dmbot_my_known_relations(g BIGINT, c TEXT) RETURNS SETOF TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT DISTINCT k.relation_id FROM memory_knowers k
        WHERE k.guild_id = g AND k.campaign_id = c AND k.relation_id IS NOT NULL
          AND k.confidence > 0 AND k.perspective_id IN (SELECT dmbot_my_perspectives(g, c))
    $fn$;

    DO $do$
    DECLARE
        f text;
    BEGIN
        FOREACH f IN ARRAY ARRAY['dmbot_my_perspectives(BIGINT, TEXT)',
                                 'dmbot_my_known_entries(BIGINT, TEXT)',
                                 'dmbot_my_known_relations(BIGINT, TEXT)'] LOOP
            EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',
                           f, current_schema());
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        END LOOP;
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
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
              AND p.proname = 'dmbot_web_relationships';
"""
    + _swaps("def", _RELATIONSHIPS_SWAPS)
    + r"""
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_graph_overview';
"""
    + _swaps("def", _OVERVIEW_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
"""
)

SQL = WEB_MY_CHARACTERS_VIEW
