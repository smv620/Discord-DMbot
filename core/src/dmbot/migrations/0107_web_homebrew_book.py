"""0107_web_homebrew_book

The page tags shared-book entries (#1579).
"""

from __future__ import annotations

WEB_HOMEBREW_BOOK = """
    -- The Homebrew page tags a shared book's entries like the Discord players' card (#1579). Since
    -- #1564 the entries players may know include ones loaded from a shared rulebook (`from_book`);
    -- the card tags them [Shared book], and [Legacy 2014] when the book is for the 2014 rules and
    -- the campaign plays a newer ruleset (`book_is_legacy`, #1563). That is a rules-edition fact,
    -- not story, and legacy content is tagged wherever it appears (CLAUDE.md, "Rules edition").
    -- Still never returned: who wrote an entry, its id, the book's name (it is never kept), or an
    -- entry the DM has not marked "players may know". Replacing a door changes no row, so no
    -- row-level security needs opening; CREATE OR REPLACE drops the function's own settings, so
    -- they are set again below. The campaigns door is unchanged.
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

SQL = WEB_HOMEBREW_BOOK
