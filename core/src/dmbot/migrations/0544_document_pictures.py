"""0544_document_pictures

The pictures of a DM's uploaded file, kept for the campaign's DMs alone (#4003 slice c; Supervisor's
ruling on #4003, 2026-11-18): two columns on `campaign_pictures` and both website doors redefined.
"""

from __future__ import annotations

# - `dm_only`: a picture taken from a DM's file (a map, a table, a handout) is the DM's material, so
#   only the campaign's DMs may open or list it: the open link answers "not found" to anyone else
#   (the API checks the DM, `web/pictures.resolve_link`), the gallery leaves it out for anyone
#   else, and it is never made public (`PictureStore.set_public`). A chat picture is never DM-only.
# - `notice_id`: the upload notice whose Undo takes the picture back (`upload_notices.id`, 32 hex
#   characters); NULL for a chat picture and for an upload that added nothing by itself. Not a
#   foreign key: a notice goes after its time while the picture stays, as anything else the upload
#   added does.
# Adding the columns with a constant default changes no row, so no row-level policy is opened.
# `dmbot_picture_by_code` now also says `dmOnly` (still nothing about the table: no campaign).
# `dmbot_picture_for_dm(tok)` is the DM check, asked as the signed-in person in the picture's
# server (`web/pictures.resolve_link`): TRUE only when the code names a DM-only picture of that
# server and the person is one of its campaign's DMs, else FALSE (one answer for every way it can
# be no). `dmbot_web_pictures` leaves DM-only pictures out for anyone but a DM, in
# the page and in its session and poster lists, and says `dmOnly` on each picture (#4761).
SQL = r"""
    ALTER TABLE campaign_pictures
        ADD COLUMN dm_only BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN notice_id TEXT CHECK (notice_id IS NULL OR notice_id ~ '^[0-9a-f]{32}$'),
        ADD CONSTRAINT campaign_pictures_dm_only_private CHECK (NOT (dm_only AND public));
    CREATE INDEX campaign_pictures_notice ON campaign_pictures (guild_id, campaign_id, notice_id)
        WHERE notice_id IS NOT NULL;

    CREATE OR REPLACE FUNCTION dmbot_picture_by_code(tok TEXT) RETURNS JSONB
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
            'guild', p.guild_id::TEXT, 'public', p.public, 'dmOnly', p.dm_only,
            'removed', p.deleted_at IS NOT NULL, 'forRoom', p.removed_for_room,
            'key', CASE WHEN p.deleted_at IS NULL THEN p.object_key ELSE '' END,
            'previewKey', CASE WHEN p.deleted_at IS NULL THEN p.preview_key ELSE '' END);
    END
    $fn$;

    CREATE FUNCTION dmbot_picture_for_dm(tok TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        p campaign_pictures%ROWTYPE;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43,}$' THEN
            RETURN FALSE;
        END IF;
        PERFORM set_config('dmbot.picture_link_code', tok, true);
        SELECT * INTO p FROM campaign_pictures WHERE code = tok;
        PERFORM set_config('dmbot.picture_link_code', '', true);
        RETURN p.id IS NOT NULL AND p.dm_only
           AND dmbot_current_guild() IS NOT DISTINCT FROM p.guild_id
           AND p.guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
           AND EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = p.guild_id AND d.campaign_id = p.campaign_id
                         AND d.user_id = dmbot_current_user());
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_pictures(
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
                   p.description, p.public, p.permanent, p.dm_only,
                   p.poster_id = dmbot_current_user() AS mine
            FROM campaign_pictures p
            LEFT JOIN transcript_sessions t
                   ON t.guild_id = p.guild_id AND t.campaign_id = p.campaign_id
                  AND t.id = p.session_id
            WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL
              AND (is_dm OR NOT p.dm_only)
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
                             'public', s.public, 'permanent', s.permanent, 'mine', s.mine,
                             'dmOnly', s.dm_only)
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
                WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL
                  AND (is_dm OR NOT p.dm_only)), '[]'::jsonb),
            'posters', COALESCE((
                SELECT jsonb_agg(DISTINCT COALESCE(
                           NULLIF(t.speaker_names ->> p.poster_id::TEXT, ''), 'Someone'))
                FROM campaign_pictures p
                LEFT JOIN transcript_sessions t
                  ON t.guild_id = p.guild_id AND t.campaign_id = p.campaign_id
                 AND t.id = p.session_id
                WHERE p.guild_id = g AND p.campaign_id = c AND p.deleted_at IS NULL
                  AND (is_dm OR NOT p.dm_only)), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_picture_by_code(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_picture_for_dm(TEXT)'
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
    REVOKE ALL ON FUNCTION dmbot_picture_for_dm(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_pictures(BIGINT, TEXT, BIGINT, TEXT, TEXT, BIGINT, BIGINT, INT)
        FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The open link's DM check for a DM-only picture (#4003 slice c); the other two are 0319's.
    ("dmbot_picture_for_dm", "dmbot_picture_for_dm(TEXT)"),
)
