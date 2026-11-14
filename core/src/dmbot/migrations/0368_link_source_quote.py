"""0368_link_source_quote

The words a kept lore or backstory connection was read from, for the graph's line panel's "Why?"
(#3606): the reader's quote, shown to the campaign's DM alone.
"""

from __future__ import annotations

SQL = r"""
    -- The reader's quote for a connection kept from an upload (the line the AI quoted, at most 240
    -- characters). This stores a few words of the upload (0352 said none): it exists only for a
    -- connection kept from an upload the DM confirmed the right to use, and is the DM's own text,
    -- so only the campaign's DM is sent it
    -- (`dmbot_web_link_sources`); a player gets the session number or "unknown", never this. ''
    -- for a connection kept before this, or from a session. Not carried in backups, as the table
    -- is not. No row is changed, so no row-level security is opened.
    ALTER TABLE memory_link_sources
        ADD COLUMN quote TEXT NOT NULL DEFAULT '' CHECK (char_length(quote) <= 240);

    CREATE OR REPLACE FUNCTION dmbot_web_link_sources(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        -- the upload label is the DM's own name for an upload and can name a private backstory:
        -- only the campaign's DM is given it, worked out here as the graph doors do
        SELECT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) INTO is_dm;
        RETURN jsonb_build_object('status', 'ok', 'links', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', r.id, 'source', r.source,
                       'label', CASE WHEN is_dm THEN s.label END,
                       'quote', CASE WHEN is_dm THEN s.quote END,
                       'at', COALESCE(ev.at, CASE WHEN r.source <> 'dm' THEN r.from_session_at END),
                       'n', n.number) ORDER BY r.id)
            FROM (SELECT * FROM memory_relations x
                  WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'
                    AND x.id = ANY (ids)
                  ORDER BY x.id LIMIT 20000) r
            LEFT JOIN memory_link_sources s
                   ON s.guild_id = g AND s.campaign_id = c AND s.relation_id = r.id
            LEFT JOIN LATERAL (
                SELECT e.session_started_at AS at FROM memory_evidence e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.relation_id = r.id
                  AND e.session_started_at IS NOT NULL
                ORDER BY e.added_at, e.id LIMIT 1) ev ON true
            LEFT JOIN (
                SELECT t.started_at, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                FROM transcript_sessions t
                WHERE t.guild_id = g AND t.campaign_id = c
                  AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) n
                   ON n.started_at = COALESCE(
                      ev.at, CASE WHEN r.source <> 'dm' THEN r.from_session_at END)), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_link_sources(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_link_sources(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """
