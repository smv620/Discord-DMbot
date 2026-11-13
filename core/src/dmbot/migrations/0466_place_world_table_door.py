"""0466_place_world_table_door

What the table may be told of a place's land, climate and ruler, for the website (#4311, the
players' half of #4287). A door any member of the server may call that gives only the rows the DM
has confirmed and marked as known to the table (`status = 'confirmed' AND table_knows`), with the
group's name for a ruler. That is a cut by the DM's own two flags, not by who may know the place:
the API then asks the gate which entries this viewer knows and applies `map_facts.for_players`,
the one players' rule, so nothing here decides who may know anything and the gate is not copied
into SQL (Supervisor's and GraphEngineer's rulings on #4287). A row DMbot only read, or one the DM
has not shown, never leaves the database for a non-DM. No row is changed.
"""

from __future__ import annotations

SQL = r"""
    CREATE FUNCTION dmbot_web_place_world_table(g BIGINT, c TEXT, place TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM memory_entities e
                       WHERE e.guild_id = g AND e.campaign_id = c AND e.id = place
                         AND e.status = 'confirmed' AND e.type = 'place'
                         AND e.merged_into IS NULL) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'rows', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', f.id, 'layer', f.layer, 'value', f.value, 'words', f.words,
                           'group', f.faction_id,
                           'groupName', (SELECT x.name FROM memory_entities x
                                         WHERE x.guild_id = g AND x.campaign_id = c
                                           AND x.id = f.faction_id),
                           'how', f.how, 'part', f.part)
                           ORDER BY f.created_at, f.id)
                FROM memory_map_facts f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.entity_id = place
                  AND f.status = 'confirmed' AND f.table_knows
                  AND f.to_session_at IS NULL), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_place_world_table(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_place_world_table(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_place_world_table", "dmbot_web_place_world_table(BIGINT, TEXT, TEXT)"),
)
