"""0352_link_sources

Where a kept connection came from, for the graph's line panel (#2946).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

LINK_SOURCES = r"""
    -- The upload a kept lore or backstory connection came from (#2946), as its label ("Tavin's
    -- backstory, 24 Oct"): nothing recorded it once the DM kept the connection, so the line panel
    -- could not say. One row per connection, written when it is kept. The label is the DM's own
    -- name for an upload and can name a private backstory, so it is sent to the DM alone: the API
    -- gives a player the session number or "unknown", never this. Words of the upload are never
    -- stored here. Not carried in backups (campaigns/tables.py); a restored campaign's connections
    -- read as added by hand.
    CREATE TABLE memory_link_sources (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        relation_id TEXT NOT NULL,
        label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 120),
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, relation_id),
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );

    -- Where the asked-for confirmed connections of a campaign came from, for the API to put
    -- through the gate (`web/link_sources.py`): the source the store recorded, the number of the
    -- session its first evidence names (or, for a connection not typed by hand, its own session
    -- time), and the upload label, given to the campaign's DM only. Ids, numbers and the label
    -- only: never a word of a transcript. Sessions are numbered as the Sessions list numbers
    -- them (an empty session takes no number: 0103). Members of the server only, as
    -- `dmbot_web_graph_source`: anyone else gets NULL. Nothing is written.
    CREATE FUNCTION dmbot_web_link_sources(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
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
    """ + _isolate("memory_link_sources")

SQL = LINK_SOURCES

ISOLATED_TABLES: tuple[str, ...] = ("memory_link_sources",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_link_sources", "dmbot_web_link_sources(BIGINT, TEXT, TEXT[])"),
)
