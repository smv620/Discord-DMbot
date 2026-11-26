"""0638_web_plan_nodes

What was read from the Shared story adventure, for the website (#3694, slice B).
"""

from __future__ import annotations

# The DM-only paged read of the chapters, places, people and links DMbot found in the DM's book,
# the same list and order as Discord's "See what was read" (`PlanStore.listing`): the nodes
# (chapters, then places, then people), then the links. **This is the book's own content**, so it
# goes only to a DM of that one campaign: anyone else, a player or a game master of another
# campaign, gets NULL, as does anyone not in the server. A page is at most 50 lines and nothing is
# written or cached. Link lines read "From → To" with what the book expects as the summary.
SQL = r"""
    CREATE FUNCTION dmbot_web_plan_nodes(g BIGINT, c TEXT, skip INTEGER, take INTEGER)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        n_nodes BIGINT;
        n_links BIGINT;
        lim INTEGER := LEAST(GREATEST(COALESCE(take, 50), 1), 50);
        off INTEGER := GREATEST(COALESCE(skip, 0), 0);
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT count(*) INTO n_nodes FROM plan_nodes
            WHERE guild_id = g AND campaign_id = c;
        SELECT count(*) INTO n_links FROM plan_links
            WHERE guild_id = g AND campaign_id = c;
        RETURN jsonb_build_object(
            'total', n_nodes + n_links,
            'items', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', i.kind, 'name', i.name, 'summary', i.summary,
                           'quote', i.quote) ORDER BY i.part, i.ord)
                FROM (
                    SELECT 0 AS part, row_number() OVER () AS ord, x.kind, x.name, x.summary,
                           x.quote
                    FROM (
                        SELECT n.kind, n.name, n.summary, n.quote FROM plan_nodes n
                        WHERE n.guild_id = g AND n.campaign_id = c
                        ORDER BY CASE n.kind WHEN 'chapter' THEN 0 WHEN 'place' THEN 1
                                 ELSE 2 END,
                                 n.document_id, n.position, n.created_at, n.name_key, n.id
                        LIMIT lim OFFSET off) x
                    UNION ALL
                    SELECT 1, row_number() OVER (), y.kind, y.name, y.summary, y.quote FROM (
                        SELECT 'link' AS kind, a.name || ' → ' || b.name AS name,
                               l.need AS summary, l.quote AS quote
                        FROM plan_links l
                        JOIN plan_nodes a ON (a.guild_id, a.campaign_id, a.id)
                                           = (l.guild_id, l.campaign_id, l.from_id)
                        JOIN plan_nodes b ON (b.guild_id, b.campaign_id, b.id)
                                           = (l.guild_id, l.campaign_id, l.to_id)
                        WHERE l.guild_id = g AND l.campaign_id = c
                        ORDER BY l.created_at, l.id
                        LIMIT GREATEST(lim - GREATEST(LEAST(n_nodes - off, lim), 0), 0)
                        OFFSET GREATEST(off - n_nodes, 0)) y
                ) i), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_plan_nodes(BIGINT, TEXT, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_plan_nodes(BIGINT, TEXT, INTEGER, INTEGER) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_plan_nodes", "dmbot_web_plan_nodes(BIGINT, TEXT, INTEGER, INTEGER)"),
)
