"""0136_session_imports

Sessions loaded from a file (#2105).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
    _setting,
)

SESSION_IMPORTS = (
    f"""
    -- Sessions a DM loaded from a file instead of recording them (#2105). The transcript itself is
    -- an ordinary stored session (transcript_sessions and transcript_lines, from people who agreed
    -- now); this row says where it came from and how far its cleaning has got. The uploaded file
    -- is never kept. Deleted with the session and the campaign.
    CREATE TABLE session_imports (
        {_memory_scope()}
        session_id  TEXT NOT NULL,
        import_id   TEXT NOT NULL CHECK (import_id ~ '^[0-9a-f]{{32}}$'),
        file_name   TEXT NOT NULL CHECK (char_length(file_name) BETWEEN 1 AND 200),
        imported_by BIGINT NOT NULL CHECK (imported_by > 0),
        imported_at BIGINT NOT NULL,
        -- waiting: stored, not cleaned yet; working: the bot is cleaning it; done; failed.
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        reason      TEXT NOT NULL DEFAULT '' CHECK (char_length(reason) <= 40),
        claimed_at  BIGINT NOT NULL DEFAULT 0,  -- when the bot took it; an old claim is retaken
        PRIMARY KEY (guild_id, session_id),
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX session_imports_open ON session_imports (state)
        WHERE state IN ('waiting', 'working');
    CREATE INDEX session_imports_batch ON session_imports (guild_id, campaign_id, import_id);
    """
    + _isolate("session_imports")
    + f"""
    -- Who a label in an import file was, remembered per campaign so the next file's "Cerric" is
    -- suggested as the same member (#2105). Only a label (as written in a file) and an account.
    CREATE TABLE import_speakers (
        {_memory_scope()}
        label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, label)
    );
    """
    + _isolate("import_speakers")
    + _setting("dmbot_import_names", "dmbot.import_names", "TEXT")
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY import_names ON web_users FOR SELECT TO %I
                USING (dmbot_import_names() = ''on'')',
            current_user);
    END
    $do$;
    -- The doors the website's role stores an import through. It has no grant on the transcript or
    -- consent tables, so each door checks the caller is a DM of the campaign in a server on the
    -- session's list, and the store door keeps only lines of members who have agreed under the
    -- terms and outside engine the caller passes (the bot's own rule, consent._select).

    -- dmbot_import_agreed: the members of the server who are recorded now (under the terms and
    -- outside engine passed) and the campaign's owner (for the plan rule). NULL unless a DM.
    CREATE FUNCTION dmbot_import_agreed(g BIGINT, c TEXT, terms INT, outside TEXT) RETURNS JSONB
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
            'agreed', COALESCE((
                SELECT jsonb_agg(x.user_id::TEXT) FROM consent x
                WHERE x.guild_id = g AND x.terms_version = terms
                  AND (outside IS NULL OR x.outside_to = outside)), '[]'::jsonb),
            'owner', (SELECT k.owner_user_id::TEXT FROM campaigns k
                      WHERE k.guild_id = g AND k.id = c));
    END
    $fn$;

    -- dmbot_import_store: one file as one stored session. `lines` is a JSON list of
    -- {ms, user, text, dur}; lines of anyone not recorded now are dropped here, whatever the
    -- caller sent. The start time moves forward a second at a time past an existing session of the
    -- campaign. Returns {session, kept, dropped} or NULL unless a DM.
    CREATE FUNCTION dmbot_import_store(
        g BIGINT, c TEXT, imp TEXT, fname TEXT, started BIGINT, lines JSONB, terms INT,
        outside TEXT
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
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN NULL;
        END IF;
        IF terms < 6 OR jsonb_typeof(lines) IS DISTINCT FROM 'array' THEN
            RETURN NULL;  -- old consent never counts, whatever the caller says (IMPORT_TERMS_FLOOR)
        END IF;
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
              AND EXISTS (SELECT 1 FROM consent x
                          WHERE x.guild_id = g AND x.user_id = l."user"
                            AND x.terms_version = terms
                            AND (outside IS NULL OR x.outside_to = outside));
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
        INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,
                                     imported_by, imported_at)
            VALUES (g, c, sid, imp, left(fname, 200), who, now_s);
        RETURN jsonb_build_object('session', sid, 'kept', kept, 'dropped', total - kept);
    END
    $fn$;


    -- dmbot_import_members: who a label can be tied to, searched by name, at most 25: the
    -- campaign's table people (each tied to a Discord account) and every other member of THIS
    -- server who is recorded now and has a name on a website account, so a new campaign has
    -- people to pick from. Never another server's people. NULL unless a DM.
    CREATE FUNCTION dmbot_import_members(g BIGINT, c TEXT, q TEXT, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        found JSONB;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- web_users is closed to everyone; a policy TO the owner role opens it only while this
        -- function has set its own setting, and only this query reads names from it.
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'userId', m.user_id::TEXT, 'name', m.name,
                       'agreed', EXISTS (SELECT 1 FROM consent x
                                         WHERE x.guild_id = g AND x.user_id = m.user_id
                                           AND x.terms_version = terms
                                           AND (outside IS NULL OR x.outside_to = outside)))
                       ORDER BY lower(m.name))
            FROM (SELECT n.name, n.user_id FROM (
                      SELECT e.name, e.played_by AS user_id, 0 AS rank
                      FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                        AND e.status = 'confirmed' AND e.played_by IS NOT NULL
                      UNION ALL
                      SELECT u.display_name, u.user_id, 1
                      FROM web_users u
                      WHERE coalesce(u.display_name, '') <> ''
                        AND EXISTS (SELECT 1 FROM consent x
                                    WHERE x.guild_id = g AND x.user_id = u.user_id
                                      AND x.terms_version = terms
                                      AND (outside IS NULL OR x.outside_to = outside))
                        AND NOT EXISTS (SELECT 1 FROM memory_entities e2
                                        WHERE e2.guild_id = g AND e2.campaign_id = c
                                          AND e2.type = 'person' AND e2.status = 'confirmed'
                                          AND e2.played_by = u.user_id)
                  ) n
                  WHERE (q = '' OR position(lower(q) IN lower(n.name)) > 0)
                  ORDER BY (lower(n.name) = lower(q)) DESC, n.rank, lower(n.name)
                  LIMIT 25) m), '[]'::jsonb) INTO found;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN found;
    END
    $fn$;

    -- dmbot_import_progress: the sessions of one import and their state. NULL unless a DM.
    CREATE FUNCTION dmbot_import_progress(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'file', i.file_name, 'state', i.state, 'reason', i.reason,
                       'date', t.started_at) ORDER BY t.started_at, i.session_id)
            FROM session_imports i JOIN transcript_sessions t
              ON t.guild_id = i.guild_id AND t.id = i.session_id
            WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = imp), '[]'::jsonb);
    END
    $fn$;

    -- dmbot_import_mapping: the remembered {label: member} of the campaign, and (with `put`) the
    -- new choices to remember, kept only for members recorded now. NULL unless a DM.
    CREATE FUNCTION dmbot_import_mapping(
        g BIGINT, c TEXT, put JSONB, terms INT, outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF put IS NOT NULL AND jsonb_typeof(put) = 'object' THEN
            INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)
                SELECT g, c, left(p.key, 80), p.value::BIGINT, now_s
                FROM jsonb_each_text(put) AS p(key, value)
                WHERE p.key <> '' AND p.value ~ '^[0-9]{1,19}$'
                  AND (length(p.value) < 19 OR p.value <= '9223372036854775807')
                  AND p.value::BIGINT > 0
                  AND terms >= 6  -- old consent never counts (IMPORT_TERMS_FLOOR)
                  AND EXISTS (SELECT 1 FROM consent x  -- only people recorded now are remembered
                              WHERE x.guild_id = g AND x.user_id = p.value::BIGINT
                                AND x.terms_version = terms
                                AND (outside IS NULL OR x.outside_to = outside))
                  AND (SELECT count(*) FROM import_speakers s2
                       WHERE s2.guild_id = g AND s2.campaign_id = c) < 200
                ON CONFLICT (guild_id, campaign_id, label)
                DO UPDATE SET user_id = EXCLUDED.user_id, updated_at = EXCLUDED.updated_at;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_object_agg(s.label, s.user_id::TEXT)
            FROM import_speakers s WHERE s.guild_id = g AND s.campaign_id = c), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_agreed(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_mapping(BIGINT, TEXT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_agreed(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_store(
        BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_mapping(BIGINT, TEXT, JSONB, INT, TEXT) FROM PUBLIC;
    """
)

SQL = SESSION_IMPORTS

ISOLATED_TABLES: tuple[str, ...] = (
    "session_imports",
    "import_speakers",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Importing older sessions (#2105): each is held to a DM of the campaign.
    ("dmbot_import_agreed", "dmbot_import_agreed(BIGINT, TEXT, INT, TEXT)"),
    (
        "dmbot_import_store",
        "dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT)",
    ),
    ("dmbot_import_members", "dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)"),
    ("dmbot_import_progress", "dmbot_import_progress(BIGINT, TEXT, TEXT)"),
    ("dmbot_import_mapping", "dmbot_import_mapping(BIGINT, TEXT, JSONB, INT, TEXT)"),
)
