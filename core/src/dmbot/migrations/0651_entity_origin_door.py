"""0651_entity_origin_door

The website's "Where was it said?" says where an entry came from, like Discord (#4557, #4554).
"""

from __future__ import annotations

# When no transcript place is kept for an entry, Discord says where it came from instead
# (`ui/name_origin.py`): the upload it was read from (its label and when it was first read, as
# `MemoryStore.name_uploads` reads them, merged entries' included), the lines a kept story
# thread's suggestion pointed at (found through the change that created the entry, as
# `ThreadSuggestionStore.kept_from` does), and whether the DM typed the name. This door gives the
# DM's page those facts. DM-only like `dmbot_web_card_lines`: anyone who is not a DM of the
# campaign gets NULL. The words of a thread's lines are read now, up to three, and only for a
# speaker with a consent row for this server at this moment; the rest never leave the database.
# Nothing is written or copied.
SQL = r"""
    CREATE FUNCTION dmbot_web_entity_origin(g BIGINT, c TEXT, eid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        ent memory_entities%ROWTYPE;
        kept RECORD;
        uploads JSONB;
        said JSONB := '[]'::jsonb;
        sess RECORD;
        number_ INT := NULL;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO ent FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = eid
              AND e.merged_into IS NULL AND e.status IN ('proposed', 'confirmed');
        IF ent.id IS NULL THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('label', u.label, 'at', u.at)
                                  ORDER BY u.at DESC, u.upload_key), '[]'::jsonb)
        INTO uploads
        FROM (
            WITH RECURSIVE family(id) AS (
                SELECT ent.id
                UNION SELECT m.id FROM memory_entities m JOIN family f ON m.merged_into = f.id
                WHERE m.guild_id = g AND m.campaign_id = c)
            SELECT s.upload_key, min(s.label) AS label, min(s.created_at) AS at
            FROM memory_name_sources s JOIN family f ON s.entity_id = f.id
            WHERE s.guild_id = g AND s.campaign_id = c
            GROUP BY s.upload_key ORDER BY at DESC, s.upload_key LIMIT 3) u;
        SELECT t.session_id, t.line_starts, t.speaker_ids, t.upload, t.created_at INTO kept
            FROM thread_suggestions t JOIN memory_changes ch
              ON ch.guild_id = t.guild_id AND ch.campaign_id = t.campaign_id
             AND ch.batch = t.kept_batch
            WHERE t.guild_id = g AND t.campaign_id = c AND t.status = 'kept'
              AND ch.table_name = 'memory_entities' AND ch.op = 'insert' AND ch.row_id = eid
            ORDER BY t.created_at DESC LIMIT 1;
        SELECT NULL::BIGINT AS started_at INTO sess;  -- (set, so a lore-read thread has none)
        IF kept.created_at IS NOT NULL AND kept.session_id IS NOT NULL
           AND cardinality(kept.line_starts) > 0 THEN
            SELECT t.started_at, t.transcripts_deleted_at AS gone,
                   (SELECT count(*) FROM transcript_sessions e
                     WHERE e.guild_id = g AND e.campaign_id = c
                       AND (e.line_count > 0 OR e.transcripts_deleted_at IS NOT NULL)
                       AND (e.started_at, e.id) <= (t.started_at, t.id)) AS number
            INTO sess
            FROM transcript_sessions t
            WHERE t.guild_id = g AND t.campaign_id = c AND t.id = kept.session_id;
            IF sess.started_at IS NOT NULL THEN
                number_ := sess.number;
            END IF;
            IF sess.started_at IS NOT NULL AND sess.gone IS NULL THEN
                SELECT COALESCE(jsonb_agg(x.said ORDER BY x.started_ms, x.id), '[]'::jsonb)
                INTO said
                FROM (
                    SELECT l.id, l.started_ms, COALESCE(l.text, l.heard) AS said
                    FROM transcript_lines l
                    WHERE l.guild_id = g AND l.session_id = kept.session_id
                      AND l.sidebar IS NULL
                      AND l.started_ms = ANY (kept.line_starts)
                      AND l.user_id = ANY (kept.speaker_ids)
                      AND EXISTS (SELECT 1 FROM consent n
                                  WHERE n.guild_id = g AND n.user_id = l.user_id)
                      AND btrim(COALESCE(l.text, l.heard)) <> ''
                    ORDER BY l.started_ms, l.id LIMIT 3) x;
            END IF;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'source', ent.source,
            'uploads', uploads,
            'thread', CASE WHEN kept.created_at IS NULL THEN NULL ELSE jsonb_build_object(
                'session', number_,
                'at', sess.started_at,
                'removed', kept.session_id IS NOT NULL AND cardinality(kept.line_starts) > 0
                           AND jsonb_array_length(said) = 0,
                'lines', said,
                'upload', kept.upload,
                'madeAt', kept.created_at) END);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_entity_origin(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_entity_origin(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_entity_origin", "dmbot_web_entity_origin(BIGINT, TEXT, TEXT)"),
)
