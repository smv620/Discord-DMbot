"""0116_web_homebrew_source

The page says where it came from (#1653).
"""

from __future__ import annotations

HOMEBREW_SOURCE_DOOR = """
    -- The website's homebrew door adds where each entry came from (#1653): how it was added and the
    -- credit as the DM chose it. `added_by` and the page's address are not sent. Replacing a door
    -- changes no row; CREATE OR REPLACE drops the function's own settings, so they are set again.
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
                           -- where it came from (#1653): the kind and the words of the credit,
                           -- never who added it (`added_by` is a Discord id: not sent)
                           'sourceKind', h.source_kind, 'creditKind', h.credit_kind,
                           'creditText', h.credit_text, 'creditDomain', h.credit_domain,
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
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = HOMEBREW_SOURCE_DOOR
