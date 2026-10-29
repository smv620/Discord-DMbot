"""0094_web_transcripts

The website reads a campaign's transcripts (#1501).
"""

from __future__ import annotations

WEB_TRANSCRIPTS = """
    -- A campaign's session transcripts on the website (#1501). Transcripts are shared: anyone in
    -- the Discord server may read and download them (CLAUDE.md, decided 2026-10-05), so these
    -- doors answer any signed-in person whose session lists the server (`dmbot_web_guilds()`), not
    -- only a DM, and only with that server set for the transaction (the caller does that for
    -- every server table). They only read. The website's role has no grant on the transcript
    -- tables. What a reader is shown (cleaned or as heard, and what each leaves out) is decided in
    -- Python by the bot's own `transcript/export.py`, so there is one formatter: these doors hand
    -- over the stored rows as they are, exactly what `TranscriptStore.lines` reads for Discord.
    --
    -- dmbot_web_transcript_sessions: the campaign's sessions that have lines, newest first, with
    -- their number (Session 1, 2, ... in order, as the bot numbers them), and the campaign's
    -- owner (the plan that allows transcripts is the owner's, #437). NULL when the caller is not
    -- in the server or the campaign does not exist.
    CREATE FUNCTION dmbot_web_transcript_sessions(g BIGINT, c TEXT) RETURNS JSONB
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
            'owner', camp.owner_user_id::TEXT,
            'sessions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'number', s.number, 'startedAt', s.started_at,
                           'endedAt', s.ended_at, 'lines', s.line_count,
                           'engines', to_jsonb(s.engines)) ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                    FROM transcript_sessions t
                    WHERE t.guild_id = g AND t.campaign_id = c AND t.line_count > 0
                ) s), '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_transcript_lines: one session's lines in the order the speech started, every
    -- column `TranscriptStore.lines` reads. NULL when the caller is not in the server or the
    -- session is not this campaign's.
    CREATE FUNCTION dmbot_web_transcript_lines(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'startedMs', l.started_ms, 'userId', l.user_id::TEXT,
                       'heard', l.heard, 'text', COALESCE(l.text, l.heard),
                       'durationMs', l.duration_ms, 'topic', l.topic,
                       'entity', COALESCE(l.entity, ''), 'sidebar', COALESCE(l.sidebar, ''),
                       'ref', l.sidebar_ref, 'replyTo', l.sidebar_reply_to,
                       'via', l.sidebar_via, 'stt', l.sidebar_stt, 'model', l.sidebar_model,
                       'prompt', l.sidebar_prompt, 'sources', l.sidebar_sources,
                       'typed', l.typed) ORDER BY l.started_ms, l.id)
            FROM transcript_lines l
            WHERE l.guild_id = g AND l.session_id = sid), '[]'::jsonb);
    END
    $fn$;
    -- dmbot_web_transcript_campaigns: the server's campaigns that have a saved transcript, with
    -- each one's owner (for the plan rule) and how many sessions; at most 100. NULL when the caller
    -- is not in the server. A person who only plays has no other list of campaigns on the site.
    CREATE FUNCTION dmbot_web_transcript_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'owner', x.owner, 'sessions', x.sessions)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name, c.owner_user_id::TEXT AS owner, count(*) AS sessions
                FROM campaigns c
                JOIN transcript_sessions t
                  ON t.guild_id = c.guild_id AND t.campaign_id = c.id AND t.line_count > 0
                WHERE c.guild_id = g
                GROUP BY c.id, c.name, c.owner_user_id
                ORDER BY lower(c.name), c.id
                LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_campaigns(BIGINT) FROM PUBLIC;
"""

SQL = WEB_TRANSCRIPTS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A campaign's session transcripts, read by any member of the server (#1501).
    ("dmbot_web_transcript_sessions", "dmbot_web_transcript_sessions(BIGINT, TEXT)"),
    (
        "dmbot_web_transcript_lines",
        "dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)",
    ),
    ("dmbot_web_transcript_campaigns", "dmbot_web_transcript_campaigns(BIGINT)"),
)
