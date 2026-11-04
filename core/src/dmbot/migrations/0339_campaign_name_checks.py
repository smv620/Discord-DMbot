"""0339_campaign_name_checks

Which of a campaign's recaps have names waiting to be checked (#3438).
"""

from __future__ import annotations

# The Questions page (#3438) shows a campaign's new names to check without asking the DM to open
# each recap: one names-to-check list for each recap that has names waiting, the same list and card
# as on the recap's own page. This door is the campaign-wide part: the recaps (by session time) that
# have at least one proposed name waiting, with how many, for a DM of the campaign only (anyone else
# gets NULL). It filters as `dmbot_recap_names_read` does in SQL (a proposed name, no secret
# spelling) and only for recaps that exist, so a link made from it always opens. The page's own read
# also hides old pronouns in Python, which SQL cannot, so the count is an upper bound: each recap's
# own list decides what the card shows. No names, lines or any text come back, nothing is written.
CAMPAIGN_NAME_CHECKS = r"""
    CREATE FUNCTION dmbot_campaign_name_checks(g BIGINT, c TEXT) RETURNS JSONB
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
            SELECT jsonb_agg(jsonb_build_object('at', t.at, 'count', t.n) ORDER BY t.at)
            FROM (
                SELECT k.session_at AS at, count(DISTINCT e.id) AS n
                FROM recap_name_checks k
                JOIN recap_jobs j
                  ON j.guild_id = k.guild_id AND j.campaign_id = k.campaign_id
                 AND j.session_at = k.session_at
                JOIN memory_entities e
                  ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id
                 AND e.id = k.entity_id
                WHERE k.guild_id = g AND k.campaign_id = c
                  AND e.status = 'proposed'
                  AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                  WHERE s.guild_id = g AND s.campaign_id = c
                                    AND s.entity_id = e.id AND s.secret)
                GROUP BY k.session_at
            ) t), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_campaign_name_checks(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_campaign_name_checks(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = CAMPAIGN_NAME_CHECKS

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_campaign_name_checks", "dmbot_campaign_name_checks(BIGINT, TEXT)"),
)
