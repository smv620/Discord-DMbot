"""0108_web_homebrew_packs

The page shows followed packs (#1586).
"""

from __future__ import annotations

WEB_HOMEBREW_PACKS = """
    -- The Homebrew page shows the packs a table follows, as the House rules page shows followed
    -- sets (#1586). A player's /dmbot rule answers from a followed pack's entries the pack marks
    -- as known to players (`known`, rules/homebrew_packs.py), so the site shows what the bot does.
    -- Per pack: its name, version and ONLY the entries marked known (kind, name, summary, text).
    -- NEVER the publisher, the share code, the publishing campaign or server, or an entry not
    -- known to players. A pack holds no book text (#1529), so there are no book tags here. The
    -- campaigns door now also counts a followed pack's known entries and says how many packs are
    -- followed. Replacing a door changes no row, so no row-level security needs opening;
    -- CREATE OR REPLACE drops the function's own settings, so they are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
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
                           'text', h.text, 'version', h.version, 'updatedAt', h.updated_at,
                           'fromBook', h.from_book,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h
                WHERE h.guild_id = g AND h.campaign_id = c AND h.players_may_know),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', t.e ->> 'kind', 'name', t.e ->> 'name',
                                          'summary', t.e -> 'summary', 'text', t.e ->> 'text')
                                          ORDER BY lower(t.e ->> 'name'), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                               WHERE jsonb_typeof(t.e) = 'object' AND t.e -> 'known' = 'true'::jsonb
                           ), '[]'::jsonb))
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'homebrew'),
                '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_homebrew_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', y.id, 'name', y.name, 'entries', y.entries, 'packs', y.packs)
                       ORDER BY lower(y.name), y.id)
            FROM (
                SELECT x.*
                FROM (
                    SELECT c.id, c.name,
                           (SELECT count(*) FROM homebrew_entries h
                            WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id
                              AND h.players_may_know)
                           + (SELECT count(*)
                              FROM rule_set_follows f,
                                   jsonb_array_elements(
                                       CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                            THEN f.content -> 'entries' ELSE '[]'::jsonb END) e
                              WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                AND f.kind = 'homebrew' AND jsonb_typeof(e) = 'object'
                                AND e -> 'known' = 'true'::jsonb) AS entries,
                           (SELECT count(*) FROM rule_set_follows f
                            WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                              AND f.kind = 'homebrew') AS packs
                    FROM campaigns c
                    WHERE c.guild_id = g
                ) x
                WHERE x.entries > 0
                ORDER BY lower(x.name), x.id LIMIT 100
            ) y), '[]'::jsonb);
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

SQL = WEB_HOMEBREW_PACKS
