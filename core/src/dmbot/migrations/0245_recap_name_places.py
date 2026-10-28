"""0245_recap_name_places

Where a name to check was said (#2403).
"""

from __future__ import annotations

RECAP_NAME_PLACES = r"""
    -- dmbot_recap_name_places: where each waiting name of a recap was said (#2403), for the
    -- campaign's DMs only. Per name, up to two windows (the earliest place and, with more than two,
    -- the middle one, as Discord's card picks them) of up to three table lines around the line the
    -- name was heard in, and how many places are left unshown. Nothing is copied: the words are
    -- read from transcript_lines now, and a line is kept only if its speaker has a consent row for
    -- this server at this moment, so someone who pressed "Stop recording me" is gone from the very
    -- next read. Such a line is `{"removed": true}` (no speaker, time or words); a window with
    -- nothing left, or whose transcript was deleted, is `available: false` with no lines. DM
    -- sidebar lines are never read (they are DMbot's answers), and in an imported session only
    -- game talk is.
    CREATE FUNCTION dmbot_recap_name_places(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        result JSONB := '{}'::jsonb;
        ent RECORD;
        ptr RECORD;
        wins JSONB;
        found JSONB;
        total INT;
        shown INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        FOR ent IN
            SELECT e.id
            FROM recap_name_checks k
            JOIN memory_entities e
              ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id AND e.id = k.entity_id
            WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
              AND e.status = 'proposed'
              AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                              WHERE s.guild_id = g AND s.campaign_id = c
                                AND s.entity_id = e.id AND s.secret)
            ORDER BY e.id
        LOOP
            wins := '[]'::jsonb;
            shown := 0;
            SELECT count(*) INTO total FROM (
                SELECT DISTINCT m.line_ref FROM memory_mentions m
                JOIN transcript_sessions t
                  ON t.guild_id = g AND t.campaign_id = c
                 AND t.id = substring(m.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$')
                WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = ent.id
                  AND m.line_ref ~ '^.*:[0-9]{1,18}:[0-9]{1,18}$') q;
            FOR ptr IN
                SELECT r.sid, r.ms, r.uid, t.started_at, t.transcripts_deleted_at AS gone,
                       (SELECT count(*) FROM transcript_sessions e
                         WHERE e.guild_id = g AND e.campaign_id = c
                           AND (e.line_count > 0 OR e.transcripts_deleted_at IS NOT NULL)
                           AND (e.started_at, e.id) <= (t.started_at, t.id)) AS number
                FROM (
                    SELECT q.*,
                           row_number() OVER (
                               ORDER BY q.at NULLS LAST, q.ms, q.sid, q.uid) AS rn,
                           count(*) OVER () AS cnt
                    FROM (
                        SELECT DISTINCT ON (m.line_ref)
                               substring(m.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$') AS sid,
                               substring(m.line_ref
                                         from ':([0-9]{1,18}):[0-9]{1,18}$')::BIGINT AS ms,
                               substring(m.line_ref from ':([0-9]{1,18})$')::BIGINT AS uid,
                               m.session_started_at AS at
                        FROM memory_mentions m
                        WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = ent.id
                          AND m.line_ref ~ '^.*:[0-9]{1,18}:[0-9]{1,18}$'
                          AND EXISTS (SELECT 1 FROM transcript_sessions t0
                                      WHERE t0.guild_id = g AND t0.campaign_id = c
                                        AND t0.id = substring(
                                            m.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$'))
                        ORDER BY m.line_ref, m.created_at, m.id) q) r
                JOIN transcript_sessions t
                  ON t.guild_id = g AND t.campaign_id = c AND t.id = r.sid
                WHERE r.cnt <= 2 OR r.rn = 1 OR r.rn = r.cnt / 2 + 1
                ORDER BY r.rn
            LOOP
                shown := shown + 1;
                found := '[]'::jsonb;
                IF ptr.gone IS NULL THEN
                    SELECT COALESCE(jsonb_agg(jsonb_build_object(
                               'ms', w.started_ms, 'uid', w.user_id::TEXT, 'hit', w.hit,
                               'agreed', w.agreed,
                               -- (words and name only for a speaker who agrees: the rest never
                               -- leave the database)
                               'text', CASE WHEN w.agreed THEN w.said ELSE '' END,
                               'name', CASE WHEN w.agreed
                                       THEN COALESCE(t2.speaker_names ->> w.user_id::TEXT, '')
                                       ELSE '' END)
                               ORDER BY w.started_ms, w.id), '[]'::jsonb)
                    INTO found
                    FROM (
                        SELECT x.*, EXISTS (
                                   SELECT 1 FROM consent n
                                   WHERE n.guild_id = g AND n.user_id = x.user_id) AS agreed
                        FROM (
                            (SELECT l.id, l.started_ms, l.user_id, false AS hit,
                                    COALESCE(l.text, l.heard) AS said
                             FROM transcript_lines l, LATERAL (
                                SELECT p.started_ms, p.id FROM transcript_lines p
                                WHERE p.guild_id = g AND p.session_id = ptr.sid
                                  AND p.started_ms = ptr.ms AND p.user_id = ptr.uid
                                  AND p.sidebar IS NULL
                                ORDER BY p.id LIMIT 1) here
                             WHERE l.guild_id = g AND l.session_id = ptr.sid AND l.sidebar IS NULL
                               AND (l.topic = 'game' OR NOT EXISTS (
                                    SELECT 1 FROM session_imports i
                                    WHERE i.guild_id = g AND i.session_id = ptr.sid))
                               AND (l.started_ms, l.id) < (here.started_ms, here.id)
                             ORDER BY l.started_ms DESC, l.id DESC LIMIT 1)
                            UNION ALL
                            (SELECT l.id, l.started_ms, l.user_id, true,
                                    COALESCE(l.text, l.heard)
                             FROM transcript_lines l
                             WHERE l.guild_id = g AND l.session_id = ptr.sid AND l.sidebar IS NULL
                               AND (l.topic = 'game' OR NOT EXISTS (
                                    SELECT 1 FROM session_imports i
                                    WHERE i.guild_id = g AND i.session_id = ptr.sid))
                               AND l.started_ms = ptr.ms AND l.user_id = ptr.uid
                             ORDER BY l.id LIMIT 1)
                            UNION ALL
                            (SELECT l.id, l.started_ms, l.user_id, false,
                                    COALESCE(l.text, l.heard)
                             FROM transcript_lines l, LATERAL (
                                SELECT p.started_ms, p.id FROM transcript_lines p
                                WHERE p.guild_id = g AND p.session_id = ptr.sid
                                  AND p.started_ms = ptr.ms AND p.user_id = ptr.uid
                                  AND p.sidebar IS NULL
                                ORDER BY p.id LIMIT 1) here
                             WHERE l.guild_id = g AND l.session_id = ptr.sid AND l.sidebar IS NULL
                               AND (l.topic = 'game' OR NOT EXISTS (
                                    SELECT 1 FROM session_imports i
                                    WHERE i.guild_id = g AND i.session_id = ptr.sid))
                               AND (l.started_ms, l.id) > (here.started_ms, here.id)
                             ORDER BY l.started_ms, l.id LIMIT 1)) x) w
                    JOIN transcript_sessions t2 ON t2.guild_id = g AND t2.id = ptr.sid;
                END IF;
                wins := wins || jsonb_build_array(jsonb_build_object(
                    'session', ptr.number, 'startedAt', ptr.started_at, 'lines', found));
            END LOOP;
            result := result || jsonb_build_object(ent.id, jsonb_build_object(
                'windows', wins, 'more', GREATEST(total - shown, 0)));
        END LOOP;
        RETURN result;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_name_places(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_name_places(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

SQL = RECAP_NAME_PLACES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_recap_name_places", "dmbot_recap_name_places(BIGINT, TEXT, BIGINT)"),
)
