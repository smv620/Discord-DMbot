"""0319_web_pictures_doors

The website's doors for kept pictures (#3274): the open link, and the campaign's list.
"""

from __future__ import annotations

from dmbot.migrations._shared import _setting

WEB_PICTURES_DOORS = (
    _setting("dmbot_picture_link_code", "dmbot.picture_link_code", "TEXT")
    + r"""
    -- The open link (`/pictures/<code>`) is read with no signed-in person and no server set, so the
    -- function that serves it needs a way past the row-level rule on campaign_pictures. As for the
    -- shared recap (0137): one read-only policy TO the role that owns the function, tied to the
    -- code the function itself set a moment ago. Never the website's role, so even a role that set
    -- the setting itself is not let in, and the function sees only the one picture the code names.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY picture_link_read ON campaign_pictures FOR SELECT TO %I
                USING (code = dmbot_picture_link_code())',
            current_user);
    END
    $do$;

    -- dmbot_picture_by_code: what the link needs to answer, or NULL for a code that names nothing
    -- (one answer for every way a code can be wrong). It never lists and never returns the poster,
    -- the description or the campaign's name: the open link shows the picture and nothing about
    -- the table. `public` says whether anyone may open it or only a signed-in person of `guild`;
    -- a removed picture answers with no file key, and `forRoom` says why it went. The file keys
    -- are for the API only, never sent on.
    CREATE FUNCTION dmbot_picture_by_code(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        p campaign_pictures%ROWTYPE;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43,}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.picture_link_code', tok, true);
        SELECT * INTO p FROM campaign_pictures WHERE code = tok;
        PERFORM set_config('dmbot.picture_link_code', '', true);
        IF p.id IS NULL THEN  -- (not FOUND: PERFORM just reset it)
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'guild', p.guild_id::TEXT, 'public', p.public,
            'removed', p.deleted_at IS NOT NULL, 'forRoom', p.removed_for_room,
            'key', CASE WHEN p.deleted_at IS NULL THEN p.object_key ELSE '' END,
            'previewKey', CASE WHEN p.deleted_at IS NULL THEN p.preview_key ELSE '' END);
    END
    $fn$;

    -- dmbot_web_pictures: the campaign's kept pictures for the gallery, to any member of its server
    -- (a private picture is private to the campaign, not to its game masters). Newest first,
    -- `lim` a page with `more` when older ones remain; `before_` and `before_id` are the made-at
    -- and id of the last one shown (0 for the first page; the id breaks a tie when several share a
    -- second); `sess_at` is a session's start (0 for all), `who` the poster's
    -- name as the session kept it ('' for everyone) and `q` a search of the descriptions ('' for
    -- none). A removed picture is not listed. The poster's name is the one the bot kept at the
    -- session (`transcript_sessions.speaker_names`, #2337); a name never kept reads 'Someone'.
    -- `usage` (bytes kept, bytes permanent) is for the campaign's game masters only. Words and
    -- counts only: no file key leaves this door.
    CREATE FUNCTION dmbot_web_pictures(
        g BIGINT, c TEXT, sess_at BIGINT, who TEXT, q TEXT, before_ BIGINT, before_id BIGINT,
        lim INT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        rows_ JSONB;
        n INT;
        per INT := GREATEST(1, LEAST(COALESCE(lim, 24), 60));
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        WITH page AS (
            SELECT p.id, p.code, p.made_at, t.started_at AS session_at,
                   COALESCE(NULLIF(t.speaker_names ->> p.poster_id::TEXT, ''), 'Someone') AS poster,
                   p.description, p.public, p.permanent,
                   p.poster_id = dmbot_current_user() AS mine
            FROM campaign_pictures p
            LEFT JOIN transcript_sessions t
                   ON t.guild_id = p.guild_id AND t.campaign_id = p.campaign_id
                  AND t.id = p.session_id
            WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL
              AND (before_ IS NULL OR before_ = 0 OR p.made_at < before_
                   OR (p.made_at = before_ AND p.id < COALESCE(before_id, 0)))
              AND (sess_at IS NULL OR sess_at = 0 OR t.started_at = sess_at)
              AND (COALESCE(who, '') = ''
                   OR COALESCE(NULLIF(t.speaker_names ->> p.poster_id::TEXT, ''), 'Someone') = who)
              AND (COALESCE(q, '') = ''
                   OR p.description ILIKE
                      '%' || replace(replace(replace(q, '\', '\\'), '%', '\%'), '_', '\_')
                          || '%')
            ORDER BY p.made_at DESC, p.id DESC
            LIMIT per + 1)
        SELECT (SELECT count(*) FROM page),
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                             'code', s.code, 'at', s.made_at, 'cursor', s.made_at || ':' || s.id,
                             'sessionAt', s.session_at,
                             'poster', s.poster, 'description', s.description,
                             'public', s.public, 'permanent', s.permanent, 'mine', s.mine)
                         ORDER BY s.made_at DESC, s.id DESC)
                         FROM (SELECT * FROM page ORDER BY made_at DESC, id DESC LIMIT per) s),
                        '[]'::jsonb)
          INTO n, rows_;
        RETURN jsonb_build_object(
            'status', 'ok', 'isDm', is_dm, 'pictures', rows_, 'more', n > per,
            'usage', CASE WHEN is_dm THEN (
                SELECT jsonb_build_object(
                    'usedBytes', COALESCE(sum(p.size_bytes + p.preview_bytes), 0),
                    'permanentBytes', COALESCE(sum(p.size_bytes + p.preview_bytes)
                                               FILTER (WHERE p.permanent), 0))
                FROM campaign_pictures p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL)
                ELSE NULL END,
            'sessions', COALESCE((
                SELECT jsonb_agg(DISTINCT t.started_at)
                FROM campaign_pictures p JOIN transcript_sessions t
                  ON t.guild_id = p.guild_id AND t.campaign_id = p.campaign_id
                 AND t.id = p.session_id
                WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL), '[]'::jsonb),
            'posters', COALESCE((
                SELECT jsonb_agg(DISTINCT COALESCE(
                           NULLIF(t.speaker_names ->> p.poster_id::TEXT, ''), 'Someone'))
                FROM campaign_pictures p
                LEFT JOIN transcript_sessions t
                  ON t.guild_id = p.guild_id AND t.campaign_id = p.campaign_id
                 AND t.id = p.session_id
                WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_picture_by_code(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pictures'
            '(BIGINT, TEXT, BIGINT, TEXT, TEXT, BIGINT, BIGINT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_picture_by_code(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_pictures(BIGINT, TEXT, BIGINT, TEXT, TEXT, BIGINT, BIGINT, INT)
        FROM PUBLIC;
"""
)

SQL = WEB_PICTURES_DOORS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The open link's lookup, by code only (#3274).
    ("dmbot_picture_by_code", "dmbot_picture_by_code(TEXT)"),
    # A campaign's kept pictures, for any member of its server (#3274).
    (
        "dmbot_web_pictures",
        "dmbot_web_pictures(BIGINT, TEXT, BIGINT, TEXT, TEXT, BIGINT, BIGINT, INT)",
    ),
)
