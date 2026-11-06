"""0114_homebrew_pack_doors

The site's doors read pointers (#1635).
"""

from __future__ import annotations

HOMEBREW_PACK_DOORS = """
    -- The website's homebrew doors and a followed pack's imported entries (#1635; docs/PLAN.md,
    -- "Homebrew (part 2)"). A pack holds an imported entry as a pointer to its page (no words), so
    -- the door reads the words from `homebrew_pages` (never a page only a backup vouched for), and
    -- an entry from a site shows only once THIS campaign approved the site, as the bot does. The
    -- count in the campaigns door follows the same rule. Replacing a door changes no row;
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
                           'kind', h.kind, 'name', h.name,
                           'summary', COALESCE(h.summary, pg.summary),
                           'text', COALESCE(h.text, pg.text), 'version', h.version,
                           'updatedAt', h.updated_at,
                           'fromBook', h.from_book, 'enabled', h.enabled,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h LEFT JOIN homebrew_pages pg ON pg.id = h.page_id
                WHERE h.guild_id = g AND h.campaign_id = c),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', COALESCE(pg.kind, t.e ->> 'kind'),
                                          'name', COALESCE(pg.name, t.e ->> 'name'),
                                          'summary', CASE WHEN pg.id IS NOT NULL
                                                          THEN to_jsonb(pg.summary)
                                                          ELSE t.e -> 'summary' END,
                                          'text', COALESCE(pg.text, t.e ->> 'text'))
                                          ORDER BY lower(COALESCE(pg.name, t.e ->> 'name')), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                                    LEFT JOIN homebrew_pages pg
                                        ON pg.url = t.e ->> 'page' AND pg.fetch_status <> 'restored'
                               WHERE jsonb_typeof(t.e) = 'object'
                                 -- an imported entry shows only if its page is kept, and an entry
                                 -- from a site only once this campaign approved the site (#1635)
                                 AND (t.e ->> 'page' IS NULL OR pg.id IS NOT NULL)
                                 AND (t.e ->> 'domain' IS NULL OR EXISTS (
                                         SELECT 1 FROM site_approvals a
                                         WHERE a.guild_id = g AND a.campaign_id = c
                                           AND a.domain = t.e ->> 'domain'))
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
                            WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id)
                           + (SELECT count(*)
                              FROM rule_set_follows f,
                                   jsonb_array_elements(
                                       CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                            THEN f.content -> 'entries' ELSE '[]'::jsonb END) e
                              WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                AND f.kind = 'homebrew' AND jsonb_typeof(e) = 'object'
                                AND (e ->> 'page' IS NULL OR EXISTS (
                                        SELECT 1 FROM homebrew_pages pg
                                        WHERE pg.url = e ->> 'page'
                                          AND pg.fetch_status <> 'restored'))
                                AND (e ->> 'domain' IS NULL OR EXISTS (
                                        SELECT 1 FROM site_approvals a
                                        WHERE a.guild_id = c.guild_id AND a.campaign_id = c.id
                                          AND a.domain = e ->> 'domain'))
                                ) AS entries,
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

SQL = HOMEBREW_PACK_DOORS
