"""0561_web_rule_update

The website's "House rule updates" (#4841 part C, Supervisor's ruling): the newer version of the
house-rules set a campaign follows, for the DM to see what changed.
"""

from __future__ import annotations

# Discord takes a followed set's newer version at a session's start and posts a note with See
# changes (`dm_screen/set_update.py`). The website shows the same changes, so it needs both
# versions' content: the copy the campaign has (`rule_set_follows.content`) and the set's latest
# version, read exactly as the bot reads it (`RuleSetStore._latest_of`): by the follow's share code
# through `dmbot_set_update_by_code` (the one guarded function that opens that set for its code,
# so no code-follow rule is copied here), else the latest version of the set in this server while
# it is still shared. The difference itself is worked out in Python by `rules.set_diff`, which
# Discord uses, so no diff rule is in SQL. Only rules and relationship definitions cross campaigns
# (CLAUDE.md, "shared rulesets"), and this door returns nothing else of the other campaign: no
# story data, no share code. DM only (NULL for anyone else), for a campaign in a server on the
# session's list. `update` is null when nothing newer waits (no followed set that still updates,
# the set stopped sharing, or no newer version). The follow's own first set only: packs are a later
# slice. The door changes no row.

SQL = r"""
    CREATE FUNCTION dmbot_web_rule_update(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        f rule_set_follows%ROWTYPE;
        latest_version INTEGER;
        latest_content JSONB;
        found_set TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO f FROM rule_set_follows r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'house_rules' AND r.updating
            ORDER BY r.position, r.followed_at, r.set_id LIMIT 1;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('update', NULL);
        END IF;
        IF f.code IS NOT NULL THEN
            SELECT u.set_id, u.version, u.content INTO found_set, latest_version, latest_content
                FROM dmbot_set_update_by_code(f.code, f.followed_at) u;
            IF NOT FOUND OR found_set IS DISTINCT FROM f.set_id THEN
                RETURN jsonb_build_object('update', NULL);
            END IF;
        ELSE
            SELECT v.version, v.content INTO latest_version, latest_content
                FROM rule_sets s
                JOIN rule_set_versions v ON v.guild_id = s.guild_id
                    AND v.campaign_id = s.campaign_id AND v.set_id = s.id
                WHERE s.guild_id = g AND s.id = f.set_id AND s.status = 'shared'
                ORDER BY v.version DESC LIMIT 1;
            IF NOT FOUND THEN
                RETURN jsonb_build_object('update', NULL);
            END IF;
        END IF;
        IF latest_version <= f.version THEN
            RETURN jsonb_build_object('update', NULL);
        END IF;
        RETURN jsonb_build_object('update', jsonb_build_object(
            'name', f.set_name, 'publisher', f.publisher, 'from', f.version,
            'to', latest_version, 'mine', f.content, 'latest', latest_content));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_update(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_update(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_rule_update", "dmbot_web_rule_update(BIGINT, TEXT)"),
)
