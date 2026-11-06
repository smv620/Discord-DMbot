"""0331_import_words_version

The uploader's own words (#3305): the version of the words the uploader ticked under is recorded
apart from the Discord terms version. `dmbot_import_store` takes it as `words`: `terms` still says
which Discord consent counts for a member (nobody is asked again when the import words change), and
`words` is what `import_consents.terms_version` keeps, "which wording the uploader saw".
"""

from __future__ import annotations

IMPORT_WORDS_VERSION = r"""
    -- The same door as 0327's, with one more argument: the version of the uploader's words.
    DROP FUNCTION dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB);
    CREATE FUNCTION dmbot_import_store(
        g BIGINT, c TEXT, imp TEXT, fname TEXT, started BIGINT, lines JSONB, terms INT,
        outside TEXT, confirmed JSONB, words INT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        sid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        at_ BIGINT := started;
        total INT;
        kept INT;
        keep JSONB;
        who BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        ticked BIGINT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN NULL;
        END IF;
        IF terms < 6 OR words < 6 OR jsonb_typeof(lines) IS DISTINCT FROM 'array'
           OR jsonb_typeof(confirmed) IS DISTINCT FROM 'array' THEN
            RETURN NULL;  -- old consent never counts, whatever the caller says (IMPORT_TERMS_FLOOR)
        END IF;
        -- Only whole numbers as text; anything else refuses the file, not a database error.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(confirmed) e
                   WHERE jsonb_typeof(e) IS DISTINCT FROM 'string'
                      OR (e #>> '{}') !~ '^-?[0-9]{1,18}$') THEN
            RETURN jsonb_build_object('error', 'unknown_speaker');
        END IF;
        ticked := ARRAY(
            SELECT DISTINCT (e #>> '{}')::BIGINT FROM jsonb_array_elements(confirmed) e);
        IF EXISTS (SELECT 1 FROM consent_refusals r
                   WHERE r.guild_id = g AND r.user_id = ANY (ticked)) THEN
            RETURN jsonb_build_object('error', 'blocked');
        END IF;
        IF EXISTS (SELECT 1 FROM unnest(ticked) u
                   WHERE u < 0 AND NOT EXISTS (
                       SELECT 1 FROM campaign_speakers s
                       WHERE s.guild_id = g AND s.campaign_id = c AND s.user_id = u))
           OR 0 = ANY (ticked) THEN
            RETURN jsonb_build_object('error', 'unknown_speaker');
        END IF;
        -- A ticked member must be someone the member search can offer: a person a character is
        -- played by, or someone with a name on a website account. A bot is neither, so a bot cannot
        -- be ticked in, whatever the page sent.
        PERFORM set_config('dmbot.import_names', 'on', true);
        IF EXISTS (SELECT 1 FROM unnest(ticked) u
                   WHERE u > 0
                     AND NOT EXISTS (SELECT 1 FROM memory_entities e
                                     WHERE e.guild_id = g AND e.campaign_id = c
                                       AND e.type = 'person' AND e.played_by = u)
                     AND NOT EXISTS (SELECT 1 FROM web_users w WHERE w.user_id = u)
                     AND NOT EXISTS (SELECT 1 FROM consent x
                                     WHERE x.guild_id = g AND x.user_id = u)) THEN
            PERFORM set_config('dmbot.import_names', '', true);
            RETURN jsonb_build_object('error', 'unknown_member');
        END IF;
        PERFORM set_config('dmbot.import_names', '', true);
        PERFORM pg_advisory_xact_lock(hashtextextended('import:' || g::TEXT || c, 0));
        total := jsonb_array_length(lines);
        SELECT coalesce(jsonb_agg(jsonb_build_object(
                   'ms', l.ms, 'user', l."user", 'text', l.text, 'dur', l.dur)
                   ORDER BY l.ms), '[]'),
               count(*)
            INTO keep, kept
            FROM jsonb_to_recordset(lines) AS l(ms BIGINT, "user" BIGINT, text TEXT, dur INT)
            WHERE l.text IS NOT NULL AND length(l.text) > 0
              AND l.ms BETWEEN 0 AND 10000000000 AND l.dur BETWEEN 0 AND 600000
              AND (l."user" = ANY (ticked)
                   OR EXISTS (SELECT 1 FROM consent x
                              WHERE x.guild_id = g AND x.user_id = l."user"
                                AND x.terms_version = terms
                                AND (outside IS NULL OR x.outside_to = outside)));
        IF kept = 0 THEN
            RETURN jsonb_build_object('session', NULL, 'kept', 0, 'dropped', total);
        END IF;
        WHILE EXISTS (SELECT 1 FROM transcript_sessions t
                      WHERE t.guild_id = g AND t.campaign_id = c AND t.started_at = at_) LOOP
            at_ := at_ + 1;
        END LOOP;
        INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at)
            VALUES (sid, g, c, at_,
                    at_ + (SELECT coalesce(max(k.ms + k.dur), 0) / 1000
                           FROM jsonb_to_recordset(keep)
                                AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT)));
        INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard, duration_ms)
            SELECT g, sid, at_ * 1000 + k.ms, k."user", k.text, k.dur
            FROM jsonb_to_recordset(keep) AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT);
        UPDATE transcript_sessions SET line_count = kept,
            speakers = ARRAY(SELECT DISTINCT k."user"
                             FROM jsonb_to_recordset(keep)
                                  AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT) ORDER BY 1)
            WHERE id = sid;
        -- A speaker without Discord is named in the session itself, as readers take names from
        -- `speaker_names` (and the start-up fill-in, which asks Discord, skips a named speaker).
        UPDATE transcript_sessions t SET speaker_names = t.speaker_names || COALESCE((
            SELECT jsonb_object_agg(s.user_id::TEXT, s.name) FROM campaign_speakers s
            WHERE s.guild_id = g AND s.campaign_id = c AND s.user_id < 0
              AND s.user_id = ANY (t.speakers)), '{}'::jsonb)
            WHERE t.id = sid;
        INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,
                                     imported_by, imported_at)
            VALUES (g, c, sid, imp, left(fname, 200), who, now_s);
        -- The record of each tick that was needed: not for a member who had agreed anyway.
        INSERT INTO import_consents (guild_id, campaign_id, id, import_id, user_id, basis,
                                     confirmed_by, confirmed_at, terms_version, lines)
            SELECT g, c, replace(gen_random_uuid()::TEXT, '-', ''), imp, k."user",
                   'uploader_confirmed', who, now_s, words, count(*)
            FROM jsonb_to_recordset(keep) AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT)
            WHERE k."user" = ANY (ticked)
              AND NOT EXISTS (SELECT 1 FROM consent x
                              WHERE x.guild_id = g AND x.user_id = k."user"
                                AND x.terms_version = terms
                                AND (outside IS NULL OR x.outside_to = outside))
            GROUP BY k."user"
            ON CONFLICT (guild_id, campaign_id, import_id, user_id)
            DO UPDATE SET lines = import_consents.lines + EXCLUDED.lines;
        RETURN jsonb_build_object('session', sid, 'kept', kept, 'dropped', total - kept);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_store('
            'BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_store(
        BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB, INT) FROM PUBLIC;
    """

SQL = IMPORT_WORDS_VERSION

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_import_store",
        "dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB, INT)",
    ),
)
