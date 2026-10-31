"""0309_web_knowers_data

What each character knows, for the website's "See as" view (#2999).
"""

from __future__ import annotations

WEB_KNOWERS_DATA = r"""
    -- "See as" (#2999) shows the DM the story graph the way the party, a player character or an
    -- NPC knows it. The knowing is worked out in Python by the one function the bot uses
    -- (`memory.gate.visible`), so the website cannot drift from Discord. The website's role has no
    -- right on the memory tables, so this one door hands that function its input: who knows each
    -- statement (and how sure), what each statement names, the groups, the player characters, and
    -- the characters a DM may pick to see as. DM only: anyone else, and a campaign that is not
    -- theirs, gets NULL. It sends ids, names of characters and numbers; never a transcript line,
    -- a note or a sheet. Rows put to sleep (`hidden_by` not empty) grant nothing, as in the bot.
    -- No row is written.
    CREATE FUNCTION dmbot_web_knowers_data(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
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
            -- the characters a DM may see as: confirmed people, player characters first
            'viewers', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'name', e.name,
                           'kind', CASE WHEN e.role = 'player_character' THEN 'pc' ELSE 'npc' END)
                       ORDER BY (e.role <> 'player_character'), lower(e.name), e.id)
                FROM (SELECT * FROM memory_entities x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'
                        AND x.status = 'confirmed' AND x.merged_into IS NULL
                      ORDER BY (x.role <> 'player_character'), lower(x.name), x.id) e),
                '[]'::jsonb),
            'playerCharacters', COALESCE((
                SELECT jsonb_agg(x.id ORDER BY x.id) FROM memory_entities x
                WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'
                  AND x.role = 'player_character' AND x.status = 'confirmed'), '[]'::jsonb),
            -- one row for each (statement, character): kind, statement id, character, certainty
            'knowers', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(
                           CASE WHEN k.about_entity_id IS NOT NULL THEN 'entry'
                                WHEN k.state_id IS NOT NULL THEN 'property'
                                WHEN k.relation_id IS NOT NULL THEN 'connection'
                                ELSE 'alias' END,
                           COALESCE(k.about_entity_id, k.state_id, k.relation_id, k.alias_id),
                           p.entity_id, k.confidence))
                FROM memory_knowers k
                JOIN memory_perspectives p ON p.id = k.perspective_id
                WHERE k.guild_id = g AND k.campaign_id = c AND cardinality(k.hidden_by) = 0
                  AND p.entity_id IS NOT NULL), '[]'::jsonb),
            'liveEntries', COALESCE((
                SELECT jsonb_agg(x.id ORDER BY x.id) FROM memory_entities x
                WHERE x.guild_id = g AND x.campaign_id = c
                  AND x.status NOT IN ('rejected', 'merged')), '[]'::jsonb),
            'states', COALESCE((
                SELECT jsonb_object_agg(s.id, s.entity_id) FROM memory_states s
                WHERE s.guild_id = g AND s.campaign_id = c), '{}'::jsonb),
            'connections', COALESCE((
                SELECT jsonb_object_agg(r.id, jsonb_build_array(r.subject_id, r.object_id))
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.status <> 'rejected'),
                '{}'::jsonb),
            'aliases', COALESCE((
                SELECT jsonb_object_agg(a.id, to_jsonb(ARRAY(
                           SELECT i FROM unnest(ARRAY[a.entity_id, a.used_by]) AS i
                           WHERE i IS NOT NULL)))
                FROM memory_aliases a
                WHERE a.guild_id = g AND a.campaign_id = c AND a.status <> 'rejected'),
                '{}'::jsonb),
            -- the rows the bot's `gate.name_entries` reads to name an entry for a viewer, so the
            -- website names exactly as Discord does: every entry (what an unnamed person hangs
            -- off and the rows joined into one included), every other name with its key and
            -- whether it is secret, and when each character learned each other name
            'entities', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', x.id, 'name', x.name, 'status', x.status,
                           'merged_into', x.merged_into, 'unnamed', x.unnamed,
                           'unnamed_anchor', x.unnamed_anchor, 'unnamed_what', x.unnamed_what,
                           'hidden_from_players', x.hidden_from_players,
                           'audience_limited', x.audience_limited) ORDER BY x.id)
                FROM memory_entities x WHERE x.guild_id = g AND x.campaign_id = c), '[]'::jsonb),
            'aliasRows', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', a.id, 'entity_id', a.entity_id, 'text', a.text, 'key', a.key,
                           'secret', a.secret) ORDER BY a.created_at, a.id)
                FROM memory_aliases a
                WHERE a.guild_id = g AND a.campaign_id = c AND a.status <> 'rejected'),
                '[]'::jsonb),
            'learned', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(k.alias_id, p.entity_id, k.created_at))
                FROM memory_knowers k
                JOIN memory_perspectives p ON p.id = k.perspective_id
                WHERE k.guild_id = g AND k.campaign_id = c AND k.alias_id IS NOT NULL
                  AND cardinality(k.hidden_by) = 0 AND p.entity_id IS NOT NULL), '[]'::jsonb),
            -- member -> group, a confirmed member_of connection
            'groups', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(r.subject_id, r.object_id))
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'member_of'
                  AND r.status = 'confirmed'), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_knowers_data(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_knowers_data(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = WEB_KNOWERS_DATA

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # What each character knows, for a campaign's game masters (#2999).
    ("dmbot_web_knowers_data", "dmbot_web_knowers_data(BIGINT, TEXT)"),
)
