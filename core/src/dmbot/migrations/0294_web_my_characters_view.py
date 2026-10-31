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
# other name). So, for a player who is not seeing everything (a DM's full view, a DM's Players View
# and a peek behind the DM screen are untouched), the two doors also list:
#   - an entry one of THEIR OWN characters knows (a row in `memory_knowers` for the entry, or for a
#     secret other name of it), named as that character knows it: the main name only if a
#     character of theirs knows the entry under its own name, else the first other name they
#     learned;
#   - a connection one of their characters knows, when both its ends are shown to them. A
#     connection that touches such an entry is shown only if a character of theirs knows it: the
#     table-wide connections of a hidden entry never come with it.
# A name belongs to the fact (Supervisor, #3037): when a join moves a connection it records the
# secret other name the connection came through (`memory_relations.via_alias_id`). A character
# that holds a row on that name sees those connections on that name's OWN node ("the guard at the
# gate", id `alias:<name id>`), whatever rows it also holds for the main entry: a character that
# knows both sees two people. The facts about the entry itself still show on its main name. The
# DM sees one entry; when the DM makes the name not secret, the connections fold into one for
# everyone. Telling one character "the guard was Ken" is a later feature.
# Only the signed-in player's own characters count (`played_by`), never another player's, and
# nothing reaches an anonymous reader (a shared link has no signed-in player). An entry the flags
# already show keeps the name everyone sees. The helpers are internal: they are not given to the
# website's role, and the doors (SECURITY DEFINER) are the only callers (they check the caller's
# server first; the helpers do not, so they must stay unreachable from outside). The SQL reads only
# what a character's own rows grant; `gate.visible_to` also honours groups, self-knowledge and
# entries a known statement names, so the website may show a player a little less than the Python
# readers do, never more. A connection's free-text `detail` is the DM's own note: a player gets it
# only when neither end is shown to them under another name.
# The doors are read back from the database as they are and widened, each anchor found exactly
# once (a drift stops the migration), as 0281 does. One column is added; no row changes.
_COMMENT = "        -- The connections the caller may see, worked out once"
_COMMENT += " (every use below reads this list)."
_SHARED_SWAPS = (
    (
        _COMMENT,
        "        flag_vis := vis;\n"
        "        IF NOT sees_all AND NOT is_dm THEN\n"
        "            SELECT COALESCE(jsonb_object_agg(m.entity_id, m.shown_name), '{}'::jsonb)\n"
        "            INTO alt_names\n"
        "            FROM dmbot_my_known_entries(g, c) m WHERE NOT (m.entity_id = ANY (vis));\n"
        "            vis := ARRAY(SELECT DISTINCT x FROM unnest(\n"
        "                vis || ARRAY(SELECT m.entity_id\n"
        "                             FROM dmbot_my_known_entries(g, c) m)) x);\n"
        "            my_rels := ARRAY(SELECT r FROM dmbot_my_known_relations(g, c) r);\n"
        "            SELECT COALESCE(jsonb_object_agg(n.alias_id, jsonb_build_object(\n"
        "                       'entity', n.entity_id, 'alias', n.alias_id,\n"
        "                       'name', n.shown_name, 'kind', n.kind)), '{}'::jsonb)\n"
        "            INTO split FROM dmbot_my_alias_nodes(g, c) n;\n"
        "            split_rels := ARRAY(SELECT r.id FROM memory_relations r\n"
        "                WHERE r.guild_id = g AND r.campaign_id = c AND r.status = 'confirmed'\n"
        "                  AND (r.secret OR r.unrevealed)\n"
        "                  AND split->>r.via_alias_id IS NOT NULL);\n"
        "            my_rels := ARRAY(SELECT x FROM unnest(my_rels) x\n"
        "                             WHERE NOT (x = ANY (split_rels)));\n"
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
    "        split JSONB := '{}'::jsonb;\n"
    "        split_rels TEXT[] := '{}';\n"
    "        out JSONB;\n"
    "        vedges JSONB := '[]'::jsonb;\n"
    "        vnodes JSONB := '[]'::jsonb;\n"
)
_NODE = "COALESCE(alt_names->>e.id, e.name)"  # the name a node is shown by

# The node's `revealed` flag says whether the DM keeps the entry from the players; a player is never
# told that (every entry they are shown is "revealed" to them), so it is true unless the caller sees
# everything (the DM's full view and a peek, as before).
_REVEALED = (
    "'revealed', NOT (e.hidden_from_players OR e.unnamed OR e.audience_limited)",
    "'revealed', NOT sees_all OR NOT (e.hidden_from_players OR e.unnamed OR e.audience_limited)",
)

# A connection's free-text `detail` is the DM's own note and could name a main name; a player gets
# it only when neither end is shown to them under another name.
_DETAIL = (
    "'detail', r.detail,",
    "'detail', CASE WHEN sees_all OR NOT EXISTS (\n"
    "                        SELECT 1 FROM memory_entities x\n"
    "                        WHERE x.guild_id = g AND x.campaign_id = c\n"
    "                          AND x.id IN (r.subject_id, r.object_id)\n"
    "                          AND alt_names->>x.id IS NOT NULL\n"
    "                          AND alt_names->>x.id <> x.name) THEN r.detail ELSE '' END,",
)

_KIND = (
    "CASE WHEN e.type = 'character' THEN CASE e.role"
    " WHEN 'player_character' THEN 'player_character'"
    " WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity' ELSE 'character' END ELSE e.type END"
)
# The facts a character knows only through a secret other name, on a node of that name. `vedges` are
# those connections with the split end replaced by the node's id ("alias:<name id>"); a node is
# listed only when one of its connections reaches something on the page.
_VIRTUAL_EDGES = (
    "SELECT jsonb_agg(jsonb_build_object(\n"
    "    'id', r.id,\n"
    "    'a', CASE WHEN r.subject_id = split->r.via_alias_id->>'entity'\n"
    "              THEN 'alias:' || r.via_alias_id ELSE r.subject_id END,\n"
    "    'b', CASE WHEN r.object_id = split->r.via_alias_id->>'entity'\n"
    "              THEN 'alias:' || r.via_alias_id ELSE r.object_id END,\n"
    "    'predicate', r.predicate, 'detail', '', 'directional', r.directional,\n"
    "    'secret', r.secret, 'at', r.created_at, 'held', NULL, 'knownBy', NULL,\n"
    "    'decided', NULL, 'revealed', false, 'pinned', false) ORDER BY r.created_at, r.id)\n"
    "FROM memory_relations r\n"
    "WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (split_rels)\n"
    "  AND r.status = 'confirmed' AND r.to_session_at IS NULL AND r.predicate <> 'led_to'\n"
    "  AND r.source NOT IN ('plan', 'story_plan')\n"
    "  AND (r.subject_id = split->r.via_alias_id->>'entity' OR r.subject_id = ANY (%(far)s))\n"
    "  AND (r.object_id = split->r.via_alias_id->>'entity' OR r.object_id = ANY (%(far)s))"
)
_VIRTUAL_NODES = (
    "SELECT jsonb_agg(jsonb_build_object('id', 'alias:' || s.key,\n"
    "    'name', s.value->>'name', 'revealed', true, 'kind', s.value->>'kind',\n"
    "    'quests', NULL, 'secret', NULL) ORDER BY s.value->>'name', s.key)\n"
    "FROM jsonb_each(split) s\n"
    "WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(vedges) v\n"
    "              WHERE v->>'a' = 'alias:' || s.key OR v->>'b' = 'alias:' || s.key)"
)

_GRAPH_TAIL = (
    "AND r.subject_id = ANY (kept) AND r.object_id = ANY (kept)), '[]'::jsonb));\n    END",
    "AND r.subject_id = ANY (kept) AND r.object_id = ANY (kept)), '[]'::jsonb));\n"
    "        IF cardinality(split_rels) > 0 THEN\n"
    "            vedges := COALESCE((" + (_VIRTUAL_EDGES % {"far": "kept"}) + "), '[]'::jsonb);\n"
    "            vnodes := COALESCE((" + _VIRTUAL_NODES + "), '[]'::jsonb);\n"
    "            out := jsonb_set(out, '{nodes}', (out->'nodes') || vnodes);\n"
    "            out := jsonb_set(out, '{edges}', (out->'edges') || vedges);\n"
    "            out := jsonb_set(out, '{names}',\n"
    "                to_jsonb((out->>'names')::int + jsonb_array_length(vnodes)));\n"
    "        END IF;\n"
    "        IF center_alias IS NOT NULL THEN\n"
    "            out := jsonb_set(out, '{center}', to_jsonb(center_alias));\n"
    "        END IF;\n"
    "        RETURN out;\n    END",
)
_GRAPH_HEAD = (
    "        RETURN jsonb_build_object(\n            'status', 'ok', 'center', start_id,",
    "        out := jsonb_build_object(\n            'status', 'ok', 'center', start_id,",
)

_OVERVIEW_HEAD = (
    "        RETURN jsonb_build_object(\n"
    "            'status', 'ok', 'canEdit', is_dm AND sees_all,",
    "        out := jsonb_build_object(\n"
    "            'status', 'ok', 'canEdit', is_dm AND sees_all,",
)
_OVERVIEW_TAIL = (
    "AND r.id = ANY (rel_ids)) d));\n    END",
    "AND r.id = ANY (rel_ids)) d));\n"
    "        IF cardinality(split_rels) > 0 THEN\n"
    "            vedges := COALESCE((" + (_VIRTUAL_EDGES % {"far": "vis"}) + "), '[]'::jsonb);\n"
    "            vnodes := COALESCE((" + _VIRTUAL_NODES + "), '[]'::jsonb);\n"
    "            out := jsonb_set(out, '{names}', (out->'names') || vnodes);\n"
    "            out := jsonb_set(out, '{pairs}', (out->'pairs') || COALESCE((\n"
    "                SELECT jsonb_agg(DISTINCT jsonb_build_array(\n"
    "                    LEAST(v->>'a', v->>'b'), GREATEST(v->>'a', v->>'b')))\n"
    "                FROM jsonb_array_elements(vedges) v), '[]'::jsonb));\n"
    "            out := jsonb_set(out, '{total}',\n"
    "                to_jsonb((out->>'total')::int + jsonb_array_length(vnodes)));\n"
    "            out := jsonb_set(out, '{connections}',\n"
    "                to_jsonb((out->>'connections')::int + jsonb_array_length(vedges)));\n"
    "        END IF;\n"
    "        RETURN out;\n    END",
)

_RELATIONSHIPS_SWAPS = (
    (
        "        kept TEXT[];\n    BEGIN",
        "        kept TEXT[];\n        center_alias TEXT;\n" + _DECLARE + "    BEGIN",
    ),
    # a centre that is a name's own node: the walk starts at the far end of one of its connections
    (
        "        IF start_id IS NULL THEN\n            SELECT x.id INTO start_id FROM (",
        "        IF start_id LIKE 'alias:%' THEN\n"
        "            center_alias := start_id;\n"
        "            start_id := (SELECT CASE\n"
        "                                 WHEN r.subject_id = split->r.via_alias_id->>'entity'\n"
        "                                 THEN r.object_id ELSE r.subject_id END\n"
        "                         FROM memory_relations r\n"
        "                         WHERE r.id = ANY (split_rels)\n"
        "                           AND 'alias:' || r.via_alias_id = center_alias\n"
        "                         ORDER BY r.created_at, r.id LIMIT 1);\n"
        "            IF start_id IS NULL OR NOT (start_id = ANY (vis)) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            END IF;\n"
        "        END IF;\n"
        "        IF start_id IS NULL THEN\n            SELECT x.id INTO start_id FROM (",
    ),
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
    _DETAIL,
    _GRAPH_HEAD,
    _GRAPH_TAIL,
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
    _OVERVIEW_HEAD,
    _OVERVIEW_TAIL,
)

WEB_MY_CHARACTERS_VIEW = (
    r"""
    -- The secret other name a join moved a connection through (#3037): a character that holds that
    -- name sees the connection on that name's own node. NULL for every connection that was not
    -- moved by a join; no row changes. (No foreign key: a name that goes leaves the column
    -- pointing at nothing, which every reader treats as "no name".)
    ALTER TABLE memory_relations ADD COLUMN via_alias_id TEXT;

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

    -- An unnamed person's label, built the way `memory/unnamed.label` builds it ("Ken's sister
    -- (name unknown)"), so a player's view can build it from an anchor name they may read.
    CREATE FUNCTION dmbot_unnamed_label(what TEXT, anchor TEXT) RETURNS TEXT
    LANGUAGE sql IMMUTABLE AS $fn$
        SELECT CASE
            WHEN anchor IS NULL OR anchor = '' THEN
                left(btrim(regexp_replace(what, '\s+', ' ', 'g')), 100) || ' (name unknown)'
            ELSE (CASE WHEN right(rtrim(left(anchor, 40)), 1) IN ('s', 'S')
                       THEN rtrim(left(anchor, 40)) || ''''
                       ELSE rtrim(left(anchor, 40)) || '''s' END)
                 || ' ' || regexp_replace(
                        left(btrim(regexp_replace(what, '\s+', ' ', 'g')), 100),
                        '^(a|an|the)\s+', '', 'i') || ' (name unknown)'
        END
    $fn$;

    -- The entries those characters know under their own name. An entry row does not make a name
    -- known when the entry's own name is a secret one: only a row on that name does.
    CREATE FUNCTION dmbot_my_own_entries(g BIGINT, c TEXT) RETURNS SETOF TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT k.about_entity_id FROM memory_knowers k
        JOIN memory_entities ke ON ke.guild_id = k.guild_id AND ke.campaign_id = k.campaign_id
                               AND ke.id = k.about_entity_id
        WHERE k.guild_id = g AND k.campaign_id = c AND k.about_entity_id IS NOT NULL
          AND k.confidence > 0 AND k.perspective_id IN (SELECT dmbot_my_perspectives(g, c))
          AND NOT EXISTS (
              SELECT 1 FROM memory_aliases so
              WHERE so.guild_id = g AND so.campaign_id = c AND so.entity_id = ke.id
                AND so.secret AND so.status <> 'rejected'
                AND regexp_replace(lower(so.text), '[^[:alnum:]]', '', 'g')
                  = regexp_replace(lower(ke.name), '[^[:alnum:]]', '', 'g'))
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
    $fn$;

    -- What an unnamed person's label may call the entry they hang off: its main name if it is a
    -- confirmed entry the players are shown by name, or one of these characters knows it by name;
    -- else NULL (the label then says "someone's").
    CREATE FUNCTION dmbot_my_anchor_name(g BIGINT, c TEXT, anchor_id TEXT) RETURNS TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT CASE WHEN en.status = 'confirmed' AND en.merged_into IS NULL
                     AND ((en.id IN (SELECT dmbot_my_own_entries(g, c)) AND NOT en.unnamed)
                      OR (NOT en.hidden_from_players AND NOT en.unnamed
                          AND NOT en.audience_limited
                          AND NOT EXISTS (
                              SELECT 1 FROM memory_aliases s
                              WHERE s.guild_id = g AND s.campaign_id = c
                                AND s.entity_id = en.id AND s.secret AND s.status <> 'rejected'
                                AND regexp_replace(lower(s.text), '[^[:alnum:]]', '', 'g')
                                  = regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g'))))
                    THEN en.name END
        FROM memory_entities en
        WHERE en.guild_id = g AND en.campaign_id = c AND en.id = anchor_id
    $fn$;

    -- An unnamed person's label as this player may read it.
    CREATE FUNCTION dmbot_my_label_of(g BIGINT, c TEXT, what TEXT, anchor_id TEXT) RETURNS TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT dmbot_unnamed_label(what, CASE WHEN anchor_id IS NULL THEN NULL ELSE
            COALESCE(dmbot_my_anchor_name(g, c, anchor_id), 'someone') END)
    $fn$;

    -- An other name of an entry as this player may read it: a joined person's label is rebuilt
    -- from what it described.
    CREATE FUNCTION dmbot_my_alias_text(g BIGINT, c TEXT, entity TEXT, alias_text TEXT)
    RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT CASE WHEN alias_text LIKE '% (name unknown)' THEN COALESCE((
            SELECT dmbot_my_label_of(g, c, m.unnamed_what, m.unnamed_anchor)
            FROM memory_entities m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.merged_into = entity
              AND m.unnamed AND m.name = alias_text LIMIT 1), 'someone (name unknown)')
            ELSE alias_text END
    $fn$;

    -- The entries those characters know, and the name each is shown by (`gate.display_name`): the
    -- main name only if one of the characters knows the entry under its own name (a row for the
    -- entry, or for the name that is its own), else the first other name they learned. An entry
    -- none of them may name is not listed. A name a join moved connections through is not an
    -- other name here: it has its own node (`dmbot_my_alias_nodes`). Confirmed entries only; the
    -- DM's kinds of note (threads, quests...) are never listed.
    CREATE FUNCTION dmbot_my_known_entries(g BIGINT, c TEXT)
    RETURNS TABLE (entity_id TEXT, shown_name TEXT, own BOOLEAN, alias_id TEXT)
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        WITH own AS (
            SELECT dmbot_my_own_entries(g, c) AS eid
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
              AND NOT EXISTS (SELECT 1 FROM memory_relations v
                              WHERE v.guild_id = g AND v.campaign_id = c
                                AND v.via_alias_id = a.id AND v.status = 'confirmed')
            GROUP BY a.entity_id, a.text, a.id
        ), first_other AS (
            SELECT DISTINCT ON (eid) eid, text, id AS aid FROM learned ORDER BY eid, at, id
        )
        SELECT e.id,
               CASE WHEN e.id IN (SELECT eid FROM own) THEN
                        CASE WHEN e.unnamed THEN dmbot_my_label_of(
                                 g, c, e.unnamed_what, e.unnamed_anchor)
                             ELSE e.name END
                    ELSE dmbot_my_alias_text(g, c, e.id, f.text) END,
               e.id IN (SELECT eid FROM own), f.aid
        FROM memory_entities e
        LEFT JOIN first_other f ON f.eid = e.id
        WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'confirmed'
          AND e.merged_into IS NULL AND e.type <> 'person'
          AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal', 'promise', 'deed')
          AND (e.id IN (SELECT eid FROM own) OR f.eid IS NOT NULL)
    $fn$;

    -- The secret other names those characters hold that a join moved connections through: each is
    -- a node of its own (the name, the entry it belongs to, and the kind of the person it
    -- described), carrying those connections.
    CREATE FUNCTION dmbot_my_alias_nodes(g BIGINT, c TEXT)
    RETURNS TABLE (alias_id TEXT, entity_id TEXT, shown_name TEXT, kind TEXT)
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT a.id, a.entity_id, dmbot_my_alias_text(g, c, a.entity_id, a.text),
               COALESCE((SELECT CASE WHEN m.type = 'character' THEN
                            CASE m.role WHEN 'player_character' THEN 'player_character'
                                        WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                        ELSE 'character' END
                        ELSE m.type END FROM memory_entities m
                         WHERE m.guild_id = g AND m.campaign_id = c AND m.merged_into = e.id
                           AND m.unnamed AND m.name = a.text LIMIT 1),
                        CASE WHEN e.type = 'character' THEN
                            CASE e.role WHEN 'player_character' THEN 'player_character'
                                        WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                        ELSE 'character' END
                        ELSE e.type END)
        FROM memory_aliases a
        JOIN memory_entities e ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                              AND e.id = a.entity_id
        WHERE a.guild_id = g AND a.campaign_id = c AND a.secret AND a.status = 'confirmed'
          AND e.status = 'confirmed' AND e.merged_into IS NULL AND e.type <> 'person'
          AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal', 'promise', 'deed')
          AND EXISTS (SELECT 1 FROM memory_knowers k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.alias_id = a.id
                        AND k.confidence > 0
                        AND k.perspective_id IN (SELECT dmbot_my_perspectives(g, c)))
          AND EXISTS (SELECT 1 FROM memory_relations v
                      WHERE v.guild_id = g AND v.campaign_id = c AND v.via_alias_id = a.id
                        AND v.status = 'confirmed')
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
                                 'dmbot_my_known_relations(BIGINT, TEXT)',
                                 'dmbot_unnamed_label(TEXT, TEXT)'] LOOP
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
