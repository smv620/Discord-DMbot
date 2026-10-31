"""0303_readings_liar

The website's list of waiting readings says who lied, and waits for that name too (#3121).
"""

from __future__ import annotations

READINGS_LIAR = r"""
    -- A suspected lie (#3091 part 2, #3120) is held by the accuser and names who lied. The DM
    -- keeps or drops it only on the website, so the list has to show the name before Keep: each
    -- reading now also carries `liar`, the name word the reading rule stored (NULL for any
    -- reading that is not a lie). The liar is a name like the other two in the claim, so while
    -- it still waits in Check new names it joins `waitingOn` and Keep stays off until it is
    -- named. Nothing else changes: DM only (anyone else gets NULL), the claim's own words, never
    -- a transcript line, and no row is written. Redefining the door drops its pinned search path,
    -- so it is set again, and PUBLIC stays revoked.
    CREATE OR REPLACE FUNCTION dmbot_web_readings(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'total', (SELECT count(*) FROM memory_claims k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                        AND k.reading IS NOT NULL AND k.how <> 'plan'
                        AND cardinality(k.speaker_ids) > 0
                        AND k.reason <> 'waiting for its name to be proposed'),
            'readings', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', m.id, 'subject', m.subject, 'relationship', m.relationship,
                    'object', m.object, 'details', m.details,
                    'at', m.session_started_at,
                    'view', m.reading->>'view', 'status', m.reading->>'status',
                    'confidence', m.reading->'confidence',
                    'liar', CASE WHEN m.reading->>'status' = 'suspected_lie'
                                 THEN m.reading->>'liar' END,
                    'waitingOn', to_jsonb(ARRAY(
                        SELECT e.name FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'proposed'
                          AND e.type <> 'person'
                          AND lower(e.name) IN (
                              lower(m.subject), lower(m.object),
                              CASE WHEN m.reading->>'status' = 'suspected_lie'
                                   THEN lower(m.reading->>'liar') END))))
                    ORDER BY m.session_started_at NULLS LAST, m.created_at, m.id)
                FROM (SELECT * FROM memory_claims k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                        AND k.reading IS NOT NULL AND k.how <> 'plan'
                        AND cardinality(k.speaker_ids) > 0
                        AND k.reason <> 'waiting for its name to be proposed'
                      ORDER BY k.session_started_at NULLS LAST, k.created_at, k.id
                      LIMIT 100) m), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_readings(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_readings(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = READINGS_LIAR
