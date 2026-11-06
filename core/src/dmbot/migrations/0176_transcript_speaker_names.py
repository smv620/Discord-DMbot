"""0176_transcript_speaker_names

Names on the website (#2337).
"""

from __future__ import annotations

TRANSCRIPT_SPEAKER_NAMES = """
    -- Each speaker's Discord display name as DMbot kept it (#2337): at the session, or for an older
    -- session when the start-up fill-in found them. The website's transcripts show these instead
    -- of "Speaker 1". A JSON object of Discord user id (text) to the name; "" means they were not
    -- in the server (not found), so DMbot does not look again. Kept on the session row: isolated
    -- with it, deleted with it and with the campaign. Added with an empty default, so no existing
    -- row changes.
    ALTER TABLE transcript_sessions ADD COLUMN speaker_names JSONB NOT NULL DEFAULT '{}';

    -- dmbot_web_transcript_speakers: the session's stored names and which of its speakers are DMs
    -- of the campaign (only people who appear in its lines, so a DM who never spoke is not named
    -- here). Any member of the server may ask, as for the transcript's lines; NULL when the caller
    -- is not in the server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_speakers(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'names', (SELECT t.speaker_names FROM transcript_sessions t
                      WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid),
            'dms', COALESCE((
                SELECT jsonb_agg(d.user_id::TEXT ORDER BY d.user_id)
                FROM campaign_dms d
                WHERE d.guild_id = g AND d.campaign_id = c
                  AND d.user_id IN (SELECT l.user_id FROM transcript_lines l
                                    WHERE l.guild_id = g AND l.session_id = sid)),
                '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_speakers(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_speakers(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

SQL = TRANSCRIPT_SPEAKER_NAMES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_transcript_speakers", "dmbot_web_transcript_speakers(BIGINT, TEXT, TEXT)"),
)
