"""0420_name_source_doors

The Names page's "found in this upload" filter (#3675 Part B, #3879): the uploads a campaign's names
were read from, and the names one or more uploads gave.
"""

from __future__ import annotations

# Two read doors over `memory_name_sources` (0402); no table, no row changed. The upload's label is
# the DM's own name for a document and can name a private backstory ("Tavin's backstory"), so both
# doors answer a DM of the campaign alone (anyone else, NULL: "not found"), as 0352 does for
# connections; a player is offered sessions only, and sessions come in a later piece.
# The choice's id is opaque: `l` and the first 16 hex of md5(upload_key), never the key itself
# (a Discord upload's key is made from its label, and a comma in it would break a list of ids).
# `dmbot_web_name_sources` gives one choice for each upload, newest first, with the label it was
# last read under. `dmbot_web_name_source_ids` gives the live entries found in the chosen uploads:
# a merge leaves the rows on the merged-away entry (0402), so each is followed forward through
# `merged_into` (at most 20 steps, a cycle cannot loop) to the entry that is live now; an entry
# that is gone is left out. The page asks the All names door for exactly those ids, and that door
# still decides what a viewer may see.
SQL = r"""
    CREATE FUNCTION dmbot_web_name_sources(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('sources', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', t.id, 'kind', 'lore', 'label', t.label)
                       ORDER BY t.last_at DESC, t.id)
            FROM (SELECT 'l' || substr(md5(s.upload_key), 1, 16) AS id,
                         (array_agg(s.label ORDER BY s.created_at DESC))[1] AS label,
                         max(s.created_at) AS last_at
                  FROM memory_name_sources s
                  WHERE s.guild_id = g AND s.campaign_id = c
                  GROUP BY s.upload_key) t), '[]'::jsonb));
    END
    $fn$;

    CREATE FUNCTION dmbot_web_name_source_ids(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            WITH RECURSIVE walk(origin, id, steps) AS (
                SELECT s.entity_id, s.entity_id, 0
                FROM memory_name_sources s
                WHERE s.guild_id = g AND s.campaign_id = c
                  AND 'l' || substr(md5(s.upload_key), 1, 16) = ANY (ids)
                UNION
                SELECT w.origin, e.merged_into, w.steps + 1
                FROM walk w
                JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c AND e.id = w.id
                WHERE e.merged_into IS NOT NULL AND w.steps < 20
            )
            SELECT jsonb_agg(DISTINCT w.id ORDER BY w.id)
            FROM walk w
            JOIN memory_entities live ON live.guild_id = g AND live.campaign_id = c
                                      AND live.id = w.id
            WHERE live.merged_into IS NULL), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_sources(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_source_ids(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_sources(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_source_ids(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_name_sources", "dmbot_web_name_sources(BIGINT, TEXT)"),
    ("dmbot_web_name_source_ids", "dmbot_web_name_source_ids(BIGINT, TEXT, TEXT[])"),
)
