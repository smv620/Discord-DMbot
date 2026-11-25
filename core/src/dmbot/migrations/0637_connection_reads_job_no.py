"""0637_connection_reads_job_no

The page that shows a connection said in words (#5293, #5302) asked for two small things.
"""

from __future__ import annotations

# `job_no` counts the jobs of one row (the read is 1, Use this 2, an Undo 3, and a retried Use after
# a failed Undo goes on counting), so the page tells a job from the one before it exactly instead of
# comparing times. The read door gives it as `jobNo`, and gives `replacing` as `{id, hidden}`:
# `hidden` is true when the connection being corrected is secret or not yet revealed *now*, so the
# page can offer "As it was before" by Discord's own rule. The three doors are replaced whole, and
# their search path set again (a replaced function loses it).
SQL = r"""
    ALTER TABLE web_connection_reads
        ADD COLUMN job_no INTEGER NOT NULL DEFAULT 1 CHECK (job_no BETWEEN 1 AND 1000);

    CREATE OR REPLACE FUNCTION dmbot_web_connection_read_get(g BIGINT, c TEXT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_connection_reads%ROWTYPE;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT f.* INTO r FROM web_connection_reads f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.id = rid
              AND f.user_id = dmbot_current_user()
              AND f.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'job', r.job, 'state', r.state, 'error', r.error,
            'first', (SELECT jsonb_build_object('id', e.id, 'name', e.name, 'kind', e.type)
                      FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = c AND e.id = r.first_id),
            'second', (SELECT jsonb_build_object('id', e.id, 'name', e.name, 'kind', e.type)
                       FROM memory_entities e
                       WHERE e.guild_id = g AND e.campaign_id = c AND e.id = r.second_id),
            'jobNo', r.job_no,
            'replacing', CASE WHEN r.replacing_id IS NULL THEN NULL ELSE jsonb_build_object(
                'id', r.replacing_id,
                'hidden', COALESCE((SELECT x.secret OR x.unrevealed FROM memory_relations x
                                    WHERE x.guild_id = g AND x.campaign_id = c
                                      AND x.id = r.replacing_id), FALSE)) END,
            'proposals', r.proposals, 'whoKnows', r.who_kind,
            'characters', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name) ORDER BY e.name)
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (r.characters)),
                '[]'::jsonb),
            'leftOut', to_jsonb(r.left_out),
            'canUndo', r.state = 'done' AND cardinality(r.batches) > 0,
            'partly', r.partly, 'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_connection_use(
        g BIGINT, c TEXT, rid TEXT, pick INT[], who TEXT, chars TEXT[])
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        r web_connection_reads%ROWTYPE;
    BEGIN
        IF me IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) THEN
            RETURN NULL;
        END IF;
        SELECT f.* INTO r FROM web_connection_reads f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.id = rid AND f.user_id = me
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.job <> 'read' OR r.state <> 'ready' THEN
            RETURN jsonb_build_object('status', 'not_ready');
        END IF;
        IF pick IS NULL OR cardinality(pick) NOT BETWEEN 1 AND 10
           OR EXISTS (SELECT 1 FROM unnest(pick) p
                      WHERE p NOT BETWEEN 1 AND jsonb_array_length(r.proposals))
           OR (SELECT count(DISTINCT p) FROM unnest(pick) p) <> cardinality(pick)
           OR who NOT IN ('everyone', 'dm', 'some', 'keep')
           OR (who = 'keep' AND r.replacing_id IS NULL)
           OR chars IS NULL OR cardinality(chars) > 10
           OR NOT (chars <@ r.characters)
           OR (who = 'some' AND cardinality(chars) = 0)
           OR (who <> 'some' AND cardinality(chars) <> 0) THEN
            RETURN jsonb_build_object('status', 'bad_choice');
        END IF;
        UPDATE web_connection_reads SET job = 'use', state = 'waiting', claimed_at = 0,
            picks = pick, use_who = who, use_chars = chars, job_no = job_no + 1,
            updated_at = now_s
            WHERE guild_id = g AND id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_connection_undo(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        r web_connection_reads%ROWTYPE;
    BEGIN
        IF me IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) THEN
            RETURN NULL;
        END IF;
        SELECT f.* INTO r FROM web_connection_reads f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.id = rid AND f.user_id = me
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.job <> 'use' OR r.state <> 'done' OR cardinality(r.batches) = 0 THEN
            RETURN jsonb_build_object('status', 'not_ready');
        END IF;
        UPDATE web_connection_reads SET job = 'undo', state = 'waiting', claimed_at = 0,
            job_no = job_no + 1, updated_at = now_s
            WHERE guild_id = g AND id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_connection_read_get(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_connection_use(BIGINT, TEXT, TEXT, INT[], TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_connection_undo(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
