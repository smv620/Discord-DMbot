"""0100_web_homebrew

The website reads a campaign's shown homebrew (#1525).
"""

from __future__ import annotations

WEB_HOMEBREW = """
    -- A campaign's homebrew on the website (#1525). Only the entries the DM marked "players may
    -- know" leave: a hidden entry (a monster the players have not met) never leaves these doors,
    -- not even for the campaign's DM, who reads those on the DM screen in Discord. Like the house
    -- rules, any signed-in person whose session lists the server may read, with that server set
    -- for the transaction; the website's role has no grant on homebrew_entries.
    --
    -- What leaves the database: kind, name, summary, text, version, updated_at (the last two
    -- only reach the API process, for the file; the page never sends them). NEVER who wrote it
    -- or its id. (0100 also withheld whether it came from a book; 0107 now tells that, as the
    -- Discord players' card does.)
    --
    -- dmbot_web_homebrew: NULL when the caller is not in the server or there is no campaign.
    CREATE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
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
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name, 'summary', h.summary,
                           'text', h.text, 'version', h.version, 'updatedAt', h.updated_at)
                           ORDER BY h.name_key)
                FROM homebrew_entries h
                WHERE h.guild_id = g AND h.campaign_id = c AND h.players_may_know),
                '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_homebrew_campaigns: the server's campaigns with at least one shown entry, with
    -- how many; at most 100. NULL when the caller is not in the server.
    CREATE FUNCTION dmbot_web_homebrew_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'entries', x.entries)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name,
                       (SELECT count(*) FROM homebrew_entries h
                        WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id
                          AND h.players_may_know) AS entries
                FROM campaigns c
                WHERE c.guild_id = g
                  AND EXISTS (SELECT 1 FROM homebrew_entries h
                              WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id
                                AND h.players_may_know)
                ORDER BY lower(c.name), c.id LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew_campaigns(BIGINT) FROM PUBLIC;
"""

SQL = WEB_HOMEBREW

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_homebrew", "dmbot_web_homebrew(BIGINT, TEXT)"),
    ("dmbot_web_homebrew_campaigns", "dmbot_web_homebrew_campaigns(BIGINT)"),
)
