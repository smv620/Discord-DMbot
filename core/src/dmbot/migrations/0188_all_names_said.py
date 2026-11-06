"""0188_all_names_said

How to say a name, for the Names page (#2317).
"""

from __future__ import annotations

ALL_NAMES_SAID = """
    -- How to say a name, for the All names page's row editor (#2317). The list door
    -- (dmbot_web_all_names) is not touched; this one answers, for a DM and for the entries just
    -- listed, the id of the entry's main name (the confirmed, public name that reads the same as
    -- the entry's own, as dmbot_web_set_said_as accepts it) and how the narrator says it. NULL for
    -- anyone who is not a DM of the campaign, so a manager never learns a spelling, and an entry
    -- with no such name is simply left out.
    CREATE FUNCTION dmbot_web_all_names_said(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        -- (a page is 20 names; a longer list is no page's, so it gets nothing)
        IF cardinality(ids) > 100
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', m.entity_id, 'aliasId', m.alias_id, 'saidAs', m.said_as))
            FROM (
                SELECT DISTINCT ON (e.id) e.id AS entity_id, a.id AS alias_id, a.said_as
                FROM memory_entities e
                JOIN memory_aliases a
                  ON a.guild_id = e.guild_id AND a.campaign_id = e.campaign_id
                 AND a.entity_id = e.id
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids)
                  AND e.status = 'confirmed' AND e.type <> 'person'
                  AND NOT a.secret AND a.status = 'confirmed'
                  AND a.kind <> 'misheard' AND a.used_by IS NULL
                  AND lower(a.text) = lower(e.name)
                ORDER BY e.id, a.id
            ) m), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_all_names_said(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_all_names_said(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
"""

SQL = ALL_NAMES_SAID

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # How a listed name is said, for the DM's row editor (#2317).
    ("dmbot_web_all_names_said", "dmbot_web_all_names_said(BIGINT, TEXT, TEXT[])"),
)
