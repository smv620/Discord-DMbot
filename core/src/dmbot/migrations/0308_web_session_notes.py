"""0308_web_session_notes

A session's notes on the website, for the campaign's game masters (#3027).
"""

from __future__ import annotations

# A beat the bot would keep (`recaps.beats.from_json`): an object with its words as text and its
# people and line pointers as lists. Anything else is left out, and the rest renumbered. Both doors
# use this one test, so the list's count is the number of beats the session page shows. (The bot
# also turns a number into text; here a beat whose words are not text is left out instead.)
_KEPT = """jsonb_typeof(e.item) = 'object'
                        AND jsonb_typeof(e.item->'what') = 'string'
                        AND jsonb_typeof(e.item->'who') = 'array'
                        AND jsonb_typeof(e.item->'lines') = 'array'
                        AND jsonb_typeof(e.item->'kind') = 'string'"""

WEB_SESSION_NOTES = r"""
    -- Session notes (#3026) are a session's beat sheet, kept for good: they survive the deletion
    -- of transcript lines and go only with the campaign. The website shows them to the campaign's
    -- game masters alone, so both doors answer NULL to anyone else (and to a stranger, and with
    -- no campaign scope), exactly as the other DM-only doors do. Words only: a beat's number, what
    -- happened, who was in it, where, and its kind. The pointers to transcript lines and PlotBot's
    -- notes about why a beat matters stay in the database. Neither door writes a row.

    -- Which sessions have notes, newest first (the page puts a link on each session it lists).
    CREATE FUNCTION dmbot_web_session_notes_list(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'sessions', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'at', n.session_at, 'writtenAt', n.written_at,
                       'beats', (SELECT count(*) FROM jsonb_array_elements(n.beats) AS e(item)
                                 WHERE {kept}))
                   ORDER BY n.session_at DESC)
            FROM (SELECT * FROM session_notes s
                  WHERE s.guild_id = g AND s.campaign_id = c
                  ORDER BY s.session_at DESC LIMIT 500) n), '[]'::jsonb));
    END
    $fn$;

    -- One session's notes: the numbered beats, in the order of the kept sheet. A damaged entry is
    -- left out and the others renumbered, as the bot reads the sheet (`beats.from_json`).
    CREATE FUNCTION dmbot_web_session_notes(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r session_notes%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT s.* INTO r FROM session_notes s
            WHERE s.guild_id = g AND s.campaign_id = c AND s.session_at = at_;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'none');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok', 'at', r.session_at, 'writtenAt', r.written_at,
            'beats', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'number', b.number, 'what', b.what, 'who', b.who,
                           'where', b.place, 'kind', b.kind) ORDER BY b.number)
                FROM (SELECT row_number() OVER (ORDER BY e.ord)::INT AS number,
                             e.item->>'what' AS what,
                             e.item->'who' AS who,
                             COALESCE(e.item->>'where', '') AS place,
                             e.item->>'kind' AS kind
                      FROM jsonb_array_elements(r.beats) WITH ORDINALITY AS e(item, ord)
                      WHERE {kept}
                      ORDER BY e.ord LIMIT 200) b), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_session_notes_list(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_session_notes(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_session_notes_list(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_session_notes(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

SQL = WEB_SESSION_NOTES.replace("{kept}", _KEPT)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A campaign's session notes, for its game masters (#3027).
    ("dmbot_web_session_notes_list", "dmbot_web_session_notes_list(BIGINT, TEXT)"),
    ("dmbot_web_session_notes", "dmbot_web_session_notes(BIGINT, TEXT, BIGINT)"),
)
