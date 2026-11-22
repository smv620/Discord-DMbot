"""0596_web_campaign_visits

My Account lists campaigns by when each was last used, most recent first (#5037, the owner's
request, 2026-11-22): the person's own last visit to a campaign on the website is one of the three
times the order comes from.
"""

from __future__ import annotations

# `web_users.campaign_visits`: a JSON object, `{"<server>.<campaign>": <Unix seconds>}`, of when
# this person last opened each campaign's pages on the website. A timestamp only: no page and no
# content. It lives on the person's own row, so it is theirs alone (the row's own isolation), goes
# when the account is deleted, and is not campaign data (not in a backup, not in any campaign
# table list). A campaign deleted leaves an entry that nothing reads; the door also drops entries
# older than 400 days whenever it writes, so the object stays small.
#
# `dmbot_web_campaign_visit(g, c)`: writes this person's visit for a campaign of server `g`, at
# most once a minute for one campaign, and only for a server in the session's own list and a
# campaign that exists there. Returns whether it wrote. No campaign data is read or returned.
SQL = r"""
    ALTER TABLE web_users ADD COLUMN campaign_visits JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(campaign_visits) = 'object');

    CREATE FUNCTION dmbot_web_campaign_visit(g BIGINT, c TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        k TEXT := g::TEXT || '.' || c;
        wrote INTEGER;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN FALSE;
        END IF;
        UPDATE web_users u
        SET campaign_visits = COALESCE((
                SELECT jsonb_object_agg(e.key, e.value)
                FROM jsonb_each(u.campaign_visits) e
                WHERE e.key <> k AND jsonb_typeof(e.value) = 'number'
                  AND (e.value #>> '{}')::BIGINT > now_s - 400 * 86400
            ), '{}'::jsonb) || jsonb_build_object(k, now_s)
        WHERE u.user_id = dmbot_current_user()
          AND COALESCE(CASE WHEN jsonb_typeof(u.campaign_visits -> k) = 'number'
                            THEN (u.campaign_visits ->> k)::BIGINT END, 0) < now_s - 60;
        GET DIAGNOSTICS wrote = ROW_COUNT;
        RETURN wrote > 0;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_campaign_visit(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_campaign_visit(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_campaign_visit", "dmbot_web_campaign_visit(BIGINT, TEXT)"),
)
