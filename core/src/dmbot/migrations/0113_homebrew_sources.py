"""0113_homebrew_sources

An entry's source, one page per address (#1633).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

HOMEBREW_SOURCES = (
    f"""
    -- Every homebrew entry records where it came from, and a campaign approves a site once
    -- (owner decision 2026-10-16, #1633; docs/PLAN.md, "Homebrew (part 2)"). No user-visible
    -- change here.
    -- `homebrew_pages`: ONE copy per published address. A page imported by link is public
    -- content, not a campaign's own data, so the table has no guild_id or campaign_id and no
    -- row-level security: the second exception to campaign isolation, next to shared rulesets
    -- (UNSCOPED_TABLES). It holds nothing about who imported it or where. The website's role
    -- has no grant on it; its doors read it as their owner. `fetch_status`: 'ok' (fetched), 'stale'
    -- (a later fetch failed, so the last good content is kept) or 'restored' (recreated from a
    -- backup file, so NOT trusted: no campaign relies on it until a fetch replaces it).
    CREATE TABLE homebrew_pages (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        url          TEXT NOT NULL UNIQUE CHECK (char_length(url) BETWEEN 1 AND 2000),
        domain       TEXT NOT NULL CHECK (char_length(domain) BETWEEN 1 AND 253),
        kind         TEXT NOT NULL CHECK (kind IN ('spell', 'monster', 'item', 'feat',
                                                   'condition', 'other')),
        name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        text         TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
        summary      TEXT CHECK (summary IS NULL OR char_length(summary) BETWEEN 1 AND 160),
        fetched_at   BIGINT NOT NULL,
        fetch_status TEXT NOT NULL DEFAULT 'ok' CHECK (fetch_status IN ('ok', 'stale', 'restored'))
    );

    -- The source of each entry: typed (the DM who typed it), upload (a file; the uploader by
    -- default, or a credit: a website, whose domain needs the same approval, or a typed name)
    -- or link (a page above; the entry's words and summary are read from the page, so they are
    -- NULL on the row). Existing entries become 'typed' by the DM who made them. The CHECK
    -- keeps each source to exactly the fields it needs.
    ALTER TABLE homebrew_entries
        ADD COLUMN source_kind   TEXT NOT NULL DEFAULT 'typed'
            CHECK (source_kind IN ('typed', 'upload', 'link')),
        ADD COLUMN added_by      BIGINT CHECK (added_by > 0),
        ADD COLUMN page_id       BIGINT REFERENCES homebrew_pages (id),
        ADD COLUMN credit_kind   TEXT CHECK (credit_kind IN ('user', 'website', 'name')),
        ADD COLUMN credit_text   TEXT CHECK (credit_text IS NULL
                                             OR char_length(credit_text) BETWEEN 1 AND 80),
        ADD COLUMN credit_domain TEXT CHECK (credit_domain IS NULL
                                             OR char_length(credit_domain) BETWEEN 1 AND 253);
    ALTER TABLE homebrew_entries ALTER COLUMN text DROP NOT NULL;
    -- Row-level security (CLAUDE.md, migrations that change rows): migrations run with no
    -- server set, so the backfill opens the table for itself and closes it again.
    CREATE POLICY migrate_backfill ON homebrew_entries USING (true) WITH CHECK (true);
    UPDATE homebrew_entries SET added_by = created_by;
    DROP POLICY migrate_backfill ON homebrew_entries;
    ALTER TABLE homebrew_entries ALTER COLUMN added_by SET NOT NULL;
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_entries_source_check CHECK (
        (source_kind = 'typed' AND page_id IS NULL AND text IS NOT NULL
            AND credit_kind IS NULL AND credit_text IS NULL AND credit_domain IS NULL)
        OR (source_kind = 'upload' AND page_id IS NULL AND text IS NOT NULL
            AND credit_kind IS NOT NULL
            AND ((credit_kind = 'user' AND credit_text IS NULL AND credit_domain IS NULL)
              OR (credit_kind = 'website' AND credit_text IS NULL AND credit_domain IS NOT NULL)
              OR (credit_kind = 'name' AND credit_text IS NOT NULL AND credit_domain IS NULL)))
        OR (source_kind = 'link' AND page_id IS NOT NULL AND text IS NULL AND summary IS NULL
            AND credit_kind IS NULL AND credit_text IS NULL AND credit_domain IS NOT NULL)
    );
    CREATE INDEX homebrew_entries_page ON homebrew_entries (page_id) WHERE page_id IS NOT NULL;

    -- A campaign approves a site once: the right-to-use warning is confirmed at the first
    -- import from it, and every later link from the same site there needs nothing more.
    -- Who approved and when is kept. Per campaign, isolated, deleted with the campaign.
    CREATE TABLE site_approvals (
        {_memory_scope()}
        domain       TEXT NOT NULL CHECK (char_length(domain) BETWEEN 1 AND 253),
        approved_by  BIGINT NOT NULL CHECK (approved_by > 0),
        approved_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, domain)
    );
    """
    + _isolate("site_approvals")
    + """

    -- The website's homebrew door reads an imported entry's words from its page. Replacing a
    -- door changes no row; CREATE OR REPLACE drops the function's own settings, so they are
    -- set again below.
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
                                          'kind', t.e ->> 'kind', 'name', t.e ->> 'name',
                                          'summary', t.e -> 'summary', 'text', t.e ->> 'text')
                                          ORDER BY lower(t.e ->> 'name'), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                               WHERE jsonb_typeof(t.e) = 'object'
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
)

SQL = HOMEBREW_SOURCES

ISOLATED_TABLES: tuple[str, ...] = ("site_approvals",)
