"""0237_web_readings

The website keeps, refuses and undoes readings (#2412).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _READING_LIST,
    WEB_WAITING_ACTIONS_LIST,
    _reading_door__2,
)

WEB_READINGS = (
    f"""
    -- What people believe, on the website (#2412 slice 3b): the DM keeps a reading as a belief,
    -- says no, or undoes a keep, through the queue of the other name actions. `target_id` is the
    -- waiting reading (a `memory_claims` row that carries one) for 'reading-place' and
    -- 'reading-no'; for 'reading-undo' it is the answer of the keep it undoes. The bot makes the
    -- very calls the DM screen would (`ClaimStore.place` / `reject`, the store's Undo), so
    -- consent, the plan and every rule are checked there, again.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         {WEB_WAITING_ACTIONS_LIST}, {_READING_LIST}));
    """
    + _reading_door__2
    + r"""
    -- The answer door says when the action was made and when it was last picked up, so the page
    -- can show how long it has waited and whether it moved (CLAUDE.md: every wait shows it).
    CREATE OR REPLACE FUNCTION dmbot_web_name_action_read(g BIGINT, c TEXT, aid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r name_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
                                  'resultId', r.result_id, 'resultOther', r.result_other,
                                  'createdAt', r.created_at, 'claimedAt', r.claimed_at,
                                  'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;

    -- The readings that wait for the DM: claims that carry a reading, not yet kept or turned
    -- down. DM only (anyone else gets NULL). The words are the claim's own (names and a
    -- relationship, as the waiting list shows them), never a transcript line.
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
                    'waitingOn', to_jsonb(ARRAY(
                        SELECT e.name FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'proposed'
                          AND e.type <> 'person'
                          AND lower(e.name) IN (lower(m.subject), lower(m.object)))))
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
    -- (redefined functions lose their pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_readings(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_readings(BIGINT, TEXT) FROM PUBLIC;
    """
)

SQL = WEB_READINGS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # What people believe: the readings that wait for the DM (#2412).
    ("dmbot_web_readings", "dmbot_web_readings(BIGINT, TEXT)"),
)
