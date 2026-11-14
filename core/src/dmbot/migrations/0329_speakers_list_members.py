"""0329_speakers_list_members

The transcripts page's "Remove this speaker's lines" list also shows the members the uploader ticked
in (#3337, #3305): someone who had not agreed in Discord, kept for this campaign on the uploader's
word, must be removable by the DM too.
"""

from __future__ import annotations

SPEAKERS_LIST_MEMBERS = r"""
    -- dmbot_import_speakers (0327), now also listing each member with an active confirmation
    -- record in this campaign (`import_consents`, not removed), by the name the member search
    -- would show (the campaign's name for the person, else the directory's, else the website's)
    -- and with `member: true`, so the page can tell them from a speaker without Discord. Names and
    -- counts only. NULL unless a DM.
    CREATE OR REPLACE FUNCTION dmbot_import_speakers(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
        found JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE(jsonb_agg(x.item ORDER BY x.is_member, lower(x.nm)), '[]'::jsonb)
            INTO found
        FROM (
            SELECT false AS is_member, s.name AS nm,
                   jsonb_build_object(
                       'id', s.user_id::TEXT, 'name', s.name,
                       'lines', (SELECT count(*) FROM transcript_lines l
                                 JOIN transcript_sessions t
                                   ON t.id = l.session_id AND t.guild_id = l.guild_id
                                 WHERE l.guild_id = g AND t.campaign_id = c
                                   AND l.user_id = s.user_id)) AS item
            FROM campaign_speakers s WHERE s.guild_id = g AND s.campaign_id = c
            UNION ALL
            SELECT true, m.name,
                   jsonb_build_object(
                       'id', m.user_id::TEXT, 'name', m.name, 'member', true,
                       'lines', (SELECT count(*) FROM transcript_lines l
                                 JOIN transcript_sessions t
                                   ON t.id = l.session_id AND t.guild_id = l.guild_id
                                 WHERE l.guild_id = g AND t.campaign_id = c
                                   AND l.user_id = m.user_id))
            FROM (
                SELECT p.user_id,
                       COALESCE((SELECT n.name FROM (
                                    SELECT e.name, 0 AS rank FROM memory_entities e
                                    WHERE e.guild_id = g AND e.campaign_id = c
                                      AND e.type = 'person' AND e.status = 'confirmed'
                                      AND e.played_by = p.user_id
                                    UNION ALL
                                    SELECT CASE WHEN d.display_name <> '' THEN d.display_name
                                                ELSE d.username END, 1
                                    FROM member_directory d
                                    WHERE d.guild_id = g AND d.user_id = p.user_id
                                      AND (d.display_name <> '' OR d.username <> '')
                                    UNION ALL
                                    SELECT u.display_name, 2 FROM web_users u
                                    WHERE u.user_id = p.user_id
                                      AND coalesce(u.display_name, '') <> ''
                                ) n ORDER BY n.rank, lower(n.name) LIMIT 1),
                                'A member') AS name
                FROM (SELECT DISTINCT c2.user_id FROM import_consents c2
                      WHERE c2.guild_id = g AND c2.campaign_id = c AND c2.user_id > 0
                        AND c2.removed_at IS NULL) p
            ) m
        ) x;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_speakers(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""

SQL = SPEAKERS_LIST_MEMBERS
