"""0181_campaign_card

A campaign's name for its page (#2416).
"""

from __future__ import annotations

CAMPAIGN_CARD = """
    -- The campaign's own page on the website (#2416) needs the campaign's name for any member of
    -- the server, a player included, and nothing else of it: not its DMs, its settings or one
    -- line of its story. NULL when the caller is not in the server (the session's own list, the
    -- same check as the other web doors) or the campaign is not there, so another server's
    -- campaign looks like none at all.
    CREATE FUNCTION dmbot_web_campaign_card(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        found_name TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT k.name INTO found_name FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('name', found_name);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_campaign_card(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_campaign_card(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = CAMPAIGN_CARD

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A campaign's own page on the website: its name, for any member of the server (#2416).
    ("dmbot_web_campaign_card", "dmbot_web_campaign_card(BIGINT, TEXT)"),
)
