"""0261_rule_sharing_read

A campaign's shared and followed rules (#2783).
"""

from __future__ import annotations

RULE_SHARING_READ = r"""
    -- What a campaign shares and follows, and the sets other campaigns share in its server, for the
    -- website's House rules page (#2783; docs/PLAN.md, "Shared rulesets"). Read only, and for a DM
    -- of the campaign alone (NULL for anyone else), in a server on the session's list. It tells
    -- the page whether a share code is on, never the code (the page does not show codes: they are
    -- read in Discord, #2460), and for the sets of other campaigns only their name, their
    -- publisher campaign's name, the version and how many rules: none of a rule's words, who
    -- wrote it or anything else of those campaigns. Only rules cross, and these are numbers.
    CREATE FUNCTION dmbot_web_rule_sharing(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        mine JSONB;
        followed JSONB;
        here JSONB;
        n_rules BIGINT;
        owner_id BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT jsonb_build_object(
                   'name', s.name, 'shared', s.status = 'shared',
                   'version', (SELECT max(v.version) FROM rule_set_versions v
                               WHERE v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                                 AND v.set_id = s.id),
                   'hasCode', EXISTS (SELECT 1 FROM rule_set_codes k
                                      WHERE k.guild_id = s.guild_id
                                        AND k.campaign_id = s.campaign_id
                                        AND k.set_id = s.id AND k.active))
            INTO mine
            FROM rule_sets s
            WHERE s.guild_id = g AND s.campaign_id = c AND s.kind = 'house_rules';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'name', f.set_name, 'publisher', f.publisher, 'version', f.version,
                   'updating', f.updating) ORDER BY f.position, f.followed_at, f.set_id),
                   '[]'::jsonb)
            INTO followed
            FROM rule_set_follows f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'house_rules';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'setId', s.id, 'name', s.name, 'publisher', p.name, 'version', v.version,
                   'rules', COALESCE(jsonb_array_length(v.content -> 'rules'), 0))
                   ORDER BY lower(s.name), s.id), '[]'::jsonb)
            INTO here
            FROM rule_sets s
            JOIN campaigns p ON p.guild_id = s.guild_id AND p.id = s.campaign_id
            JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                AND v.set_id = s.id
                AND v.version = (SELECT max(m.version) FROM rule_set_versions m
                                 WHERE m.guild_id = s.guild_id AND m.campaign_id = s.campaign_id
                                   AND m.set_id = s.id)
            WHERE s.guild_id = g AND s.status = 'shared' AND s.kind = 'house_rules'
              AND s.campaign_id <> c;
        SELECT count(*) INTO n_rules FROM house_rules r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.from_set IS NULL;
        SELECT k.owner_user_id INTO owner_id FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        -- The owner goes to the API only to judge the plan; the page is told yes or no.
        RETURN jsonb_build_object('published', mine, 'following', followed, 'here', here,
                                  'ruleCount', n_rules, 'owner', owner_id::TEXT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_sharing(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_sharing(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = RULE_SHARING_READ

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_rule_sharing", "dmbot_web_rule_sharing(BIGINT, TEXT)"),  # (#2783)
)
