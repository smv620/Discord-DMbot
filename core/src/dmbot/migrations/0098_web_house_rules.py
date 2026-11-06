"""0098_web_house_rules

The website reads a campaign's house rules (#1521).
"""

from __future__ import annotations

WEB_HOUSE_RULES = """
    -- A campaign's house rules on the website (#1521). They are the table's rules and every member
    -- of the Discord server may read them with /dmbot houserules (the bot does not gate that by
    -- plan), so these two doors answer any signed-in person whose session lists the server
    -- (`dmbot_web_guilds()`), with that server set for the transaction, and only read. The
    -- website's role has no grant on house_rules or rule_set_follows.
    --
    -- What leaves: each rule's number, words, the book rule it replaces and whether it was merged
    -- in from a set; the followed house-rules sets' name, version and rules (the bot cites them as
    -- "<set> house rules, vN"). NEVER a rule's scenario (the story event behind it: the Discord
    -- list does not show it), who wrote it, the session it came from, a set's publisher or share
    -- code, or the campaign's linked rules document (a private share link the bot shows only to
    -- its DMs).
    --
    -- dmbot_web_house_rules: NULL when the caller is not in the server or there is no campaign.
    CREATE FUNCTION dmbot_web_house_rules(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'rules', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'number', r.number, 'rule', r.rule, 'supersedes', r.supersedes,
                           'fromSet', r.from_set) ORDER BY r.number DESC)
                FROM house_rules r WHERE r.guild_id = g AND r.campaign_id = c), '[]'::jsonb),
            'sets', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'rules', CASE WHEN jsonb_typeof(f.content -> 'rules') = 'array'
                                         THEN f.content -> 'rules' ELSE '[]'::jsonb END)
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'house_rules'),
                '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_house_rules_campaigns: the server's campaigns that have a house rule or follow a
    -- house-rules set, with how many of each; at most 100. NULL when the caller is not in the
    -- server. A person who only plays has no other list of campaigns on the site.
    CREATE FUNCTION dmbot_web_house_rules_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'rules', x.rules, 'sets', x.sets)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name,
                       (SELECT count(*) FROM house_rules r
                        WHERE r.guild_id = c.guild_id AND r.campaign_id = c.id) AS rules,
                       (SELECT count(*) FROM rule_set_follows f
                        WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                          AND f.kind = 'house_rules') AS sets
                FROM campaigns c
                WHERE c.guild_id = g
                  AND (EXISTS (SELECT 1 FROM house_rules r
                               WHERE r.guild_id = c.guild_id AND r.campaign_id = c.id)
                       OR EXISTS (SELECT 1 FROM rule_set_follows f
                                  WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                    AND f.kind = 'house_rules'))
                ORDER BY lower(c.name), c.id LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_house_rules(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_house_rules_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_house_rules(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_house_rules_campaigns(BIGINT) FROM PUBLIC;
"""

SQL = WEB_HOUSE_RULES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A campaign's house rules, read by any member of the server (#1521).
    ("dmbot_web_house_rules", "dmbot_web_house_rules(BIGINT, TEXT)"),
    ("dmbot_web_house_rules_campaigns", "dmbot_web_house_rules_campaigns(BIGINT)"),
)
