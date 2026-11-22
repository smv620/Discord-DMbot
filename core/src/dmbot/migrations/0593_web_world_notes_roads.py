"""0593_web_world_notes_roads

World notes on the website lists a known road's condition DMbot read, as Discord's World notes does
(#5003, item 4 of #4451; #4258 slice 4a).
"""

from __future__ import annotations

# `dmbot_web_world_notes` (0481) joined the map facts on `entity_id`, so a known road's condition (a
# row with `relation_id` set and `entity_id` NULL, 0500) never showed. The place group now also has
# those rows, as `place:<fact id>` lines like a place's own, so ✅ Keep and 🚫 Not that run the same
# store calls as for any other place line. The line's `name` is "the road between A and B" as
# Discord says it, and its `entityId` is the road's first place. A road counts only while the DM has
# not taken it away or ended it, between two kept places, as `world_notes.read` counts it (the
# trigger of 0500 already keeps a condition to a known road). Nothing else about the door changes
# (only the DM, DMbot's reads only, open rows only); no row changes. 0481 is the only migration
# that defines the function, so it is written out again whole.
SQL = r"""
    CREATE OR REPLACE FUNCTION dmbot_web_world_notes(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'groups', jsonb_build_array(
                jsonb_build_object('kind', 'place', 'lines', COALESCE((
                    SELECT jsonb_agg(x.line ORDER BY x.created_at, x.id)
                    FROM (
                    SELECT f.created_at, f.id, jsonb_build_object(
                               'id', 'place:' || f.id, 'kind', 'place', 'entityId', f.entity_id,
                               'name', e.name, 'layer', f.layer,
                               'value', CASE WHEN f.layer = 'control' THEN f.faction_id
                                             ELSE f.value END,
                               'words', f.words, 'part', f.part,
                               'how', NULLIF(f.how, ''), 'ruler', r.name,
                               'fromSession', f.from_session_at,
                               'from', CASE WHEN f.from_session_at IS NULL THEN 'lore'
                                            ELSE 'session' END) AS line
                    FROM memory_map_facts f
                    JOIN memory_entities e
                      ON e.guild_id = f.guild_id AND e.campaign_id = f.campaign_id
                     AND e.id = f.entity_id AND e.status IN ('confirmed', 'proposed')
                     AND e.merged_into IS NULL
                    LEFT JOIN memory_entities r
                      ON r.guild_id = f.guild_id AND r.campaign_id = f.campaign_id
                     AND r.id = f.faction_id AND r.status IN ('confirmed', 'proposed')
                     AND r.merged_into IS NULL
                    WHERE f.guild_id = g AND f.campaign_id = c AND f.status = 'read'
                      AND f.to_session_at IS NULL
                      AND (f.layer <> 'control' OR r.id IS NOT NULL)
                    UNION ALL
                    SELECT f.created_at, f.id, jsonb_build_object(
                               'id', 'place:' || f.id, 'kind', 'place', 'entityId', r.subject_id,
                               'name', 'the road between ' || ea.name || ' and ' || eb.name,
                               'layer', f.layer, 'value', f.value, 'words', f.words,
                               'part', f.part, 'how', NULLIF(f.how, ''),
                               'ruler', NULL, 'fromSession', f.from_session_at,
                               'from', CASE WHEN f.from_session_at IS NULL THEN 'lore'
                                            ELSE 'session' END) AS line
                    FROM memory_map_facts f
                    JOIN memory_relations r
                      ON r.guild_id = f.guild_id AND r.campaign_id = f.campaign_id
                     AND r.id = f.relation_id AND r.status <> 'rejected'
                     AND r.to_session_at IS NULL
                    JOIN memory_entities ea
                      ON ea.guild_id = r.guild_id AND ea.campaign_id = r.campaign_id
                     AND ea.id = r.subject_id AND ea.status IN ('confirmed', 'proposed')
                     AND ea.merged_into IS NULL
                    JOIN memory_entities eb
                      ON eb.guild_id = r.guild_id AND eb.campaign_id = r.campaign_id
                     AND eb.id = r.object_id AND eb.status IN ('confirmed', 'proposed')
                     AND eb.merged_into IS NULL
                    WHERE f.guild_id = g AND f.campaign_id = c AND f.status = 'read'
                      AND f.to_session_at IS NULL AND f.layer = 'condition'
                      AND f.relation_id IS NOT NULL) x), '[]'::jsonb)),
                jsonb_build_object('kind', 'language', 'lines', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                               'id', 'language:' || s.id, 'kind', 'language',
                               'entityId', s.entity_id, 'name', e.name, 'value', s.name,
                               'fromSession', NULL, 'from', 'lore')
                               ORDER BY s.created_at, s.id)
                    FROM memory_story_languages s
                    JOIN memory_entities e
                      ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id
                     AND e.id = s.entity_id AND e.status IN ('confirmed', 'proposed')
                     AND e.merged_into IS NULL
                    WHERE s.guild_id = g AND s.campaign_id = c AND s.given_by = 'read'
                      AND NOT EXISTS (SELECT 1 FROM character_sheets h
                                      WHERE h.guild_id = s.guild_id
                                        AND h.campaign_id = s.campaign_id
                                        AND h.entity_id = s.entity_id)
                      AND NOT EXISTS (SELECT 1 FROM memory_trait_offers o
                                      WHERE o.guild_id = s.guild_id
                                        AND o.campaign_id = s.campaign_id
                                        AND o.entity_id = s.entity_id AND o.kind = 'language'
                                        AND o.name_key = s.name_key)), '[]'::jsonb))));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_world_notes(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_world_notes(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
