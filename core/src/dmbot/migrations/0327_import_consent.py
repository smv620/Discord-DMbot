"""0327_import_consent

Importing past sessions with the uploader's consent per speaker (#3305; docs/PLAN.md, "Import
sessions → Consent"): a record of every "no", speakers without Discord, and the record of what the
uploader confirmed.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

IMPORT_CONSENT = (
    r"""
    -- A member's "no" to being recorded (#3305). Until now a "no" left no trace: Stop recording me
    -- deletes the consent row and the Decline button saves nothing, so nothing could tell a refusal
    -- from a person who never answered. The import refuses to let an uploader tick in someone who
    -- said no, so the bot writes a row here from every path that says no (`ConsentStore.revoke`)
    -- and removes it when the person says yes again. Per server, not in a campaign backup (it is
    -- the person's own choice about a server, not campaign content). A "no" said before this
    -- existed is not known. `stopped`: they had agreed and stopped; `declined`: they turned DMbot
    -- down without ever agreeing.
    CREATE TABLE consent_refusals (
        guild_id   BIGINT NOT NULL,
        -- (no range check on purpose: saying no must never fail to be saved)
        user_id    BIGINT NOT NULL,
        kind       TEXT NOT NULL CHECK (kind IN ('stopped', 'declined')),
        refused_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """
    + _isolate("consent_refusals")
    + r"""
    -- A speaker of one campaign who has no Discord account (#3305): someone who spoke in an
    -- imported file and is not on Discord. Its id is a NEGATIVE number (-1, -2, -3...) within the
    -- campaign. A Discord id is always positive, so the two can never meet in a column that
    -- holds either (`transcript_lines.user_id` and everything that reads it), and no mention is
    -- ever built from one. The number is per campaign because every line belongs to a session of
    -- one campaign and every reader looks up by (server, campaign); it also means a restore keeps
    -- the numbers as they are.
    CREATE TABLE campaign_speakers (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id < 0),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX campaign_speakers_name
        ON campaign_speakers (guild_id, campaign_id, lower(name));
    """
    + _isolate("campaign_speakers")
    + r"""
    -- What the uploader confirmed (#3305): for one import, one row per speaker (a member who had
    -- not agreed in Discord, or a speaker without Discord) saying who confirmed it, when, and under
    -- which version of the consent wording. "Confirmed by the uploader" is only ever recorded
    -- here; "agreed in Discord" stays the `consent` table. Kept as long as the lines are (it goes
    -- with the campaign) and in its backup.
    CREATE TABLE import_consents (
        guild_id      BIGINT NOT NULL,
        campaign_id   TEXT NOT NULL,
        id            TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        import_id     TEXT NOT NULL CHECK (char_length(import_id) BETWEEN 1 AND 64),
        user_id       BIGINT NOT NULL CHECK (user_id <> 0),
        basis         TEXT NOT NULL CHECK (basis IN ('uploader_confirmed')),
        confirmed_by  BIGINT NOT NULL CHECK (confirmed_by > 0),
        confirmed_at  BIGINT NOT NULL,
        terms_version INT NOT NULL CHECK (terms_version >= 1),
        lines         INT NOT NULL DEFAULT 0 CHECK (lines >= 0),
        PRIMARY KEY (guild_id, id),
        UNIQUE (guild_id, campaign_id, import_id, user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX import_consents_speaker ON import_consents (guild_id, campaign_id, user_id);
    """
    + _isolate("import_consents")
    + r"""
    -- A line's speaker may now be a negative speaker without Discord. (The other `user_id > 0`
    -- checks on import tables are about a member: a mapping to a member, or the person who asked.)
    ALTER TABLE web_line_fixes DROP CONSTRAINT web_line_fixes_speaker_check;
    ALTER TABLE web_line_fixes ADD CONSTRAINT web_line_fixes_speaker_check CHECK (speaker <> 0);

    -- dmbot_import_agreed, now also with who said no and the campaign's speakers without Discord,
    -- so the page greys out a refused member and the preview can match a label to an existing
    -- speaker by name. NULL unless a DM.
    CREATE OR REPLACE FUNCTION dmbot_import_agreed(g BIGINT, c TEXT, terms INT, outside TEXT)
    RETURNS JSONB
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
            'refused', COALESCE((
                SELECT jsonb_object_agg(r.user_id::TEXT, r.kind) FROM consent_refusals r
                WHERE r.guild_id = g), '{}'::jsonb),
            'speakers', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', s.user_id::TEXT, 'name', s.name)
                                 ORDER BY lower(s.name))
                FROM campaign_speakers s
                WHERE s.guild_id = g AND s.campaign_id = c), '[]'::jsonb),
            'owner', (SELECT k.owner_user_id::TEXT FROM campaigns k
                      WHERE k.guild_id = g AND k.id = c));
    END
    $fn$;

    -- dmbot_import_speaker: the campaign's speaker without Discord with this name, made if there
    -- is none (the same name, ignoring case, is the same speaker in the next file). Returns its
    -- negative id. NULL unless a DM, or when the name is empty or over 80 characters, or the
    -- campaign already has 500 of them.
    CREATE FUNCTION dmbot_import_speaker(g BIGINT, c TEXT, nm TEXT) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        who BIGINT := dmbot_current_user();
        clean TEXT := btrim(regexp_replace(coalesce(nm, ''), '\s+', ' ', 'g'));
        found BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN NULL;
        END IF;
        IF char_length(clean) NOT BETWEEN 1 AND 80 THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('speakers:' || g::TEXT || c, 0));
        SELECT s.user_id INTO found FROM campaign_speakers s
            WHERE s.guild_id = g AND s.campaign_id = c AND lower(s.name) = lower(clean);
        IF found IS NOT NULL THEN
            RETURN found;
        END IF;
        IF (SELECT count(*) FROM campaign_speakers s
            WHERE s.guild_id = g AND s.campaign_id = c) >= 500 THEN
            RETURN NULL;
        END IF;
        INSERT INTO campaign_speakers (guild_id, campaign_id, user_id, name, created_by, created_at)
            VALUES (g, c,
                    (SELECT coalesce(min(s.user_id), 0) - 1 FROM campaign_speakers s
                     WHERE s.guild_id = g AND s.campaign_id = c),
                    clean, who, floor(extract(epoch FROM clock_timestamp()))::BIGINT)
            RETURNING user_id INTO found;
        RETURN found;
    END
    $fn$;

    -- dmbot_import_store, now taking `confirmed`: a JSON list of the ids the uploader ticked (a
    -- member who has not agreed, or a speaker without Discord). Checked here, whatever the page
    -- said: a member who said no (consent_refusals) refuses the whole file, a negative id must be
    -- one of this campaign's speakers, and only a line of an agreed member or a ticked id is
    -- kept. Each ticked id with lines kept gets its record. Returns {session, kept, dropped} or
    -- {error: 'blocked' | 'unknown_speaker'}, or NULL unless a DM.
    DROP FUNCTION dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT);
    CREATE FUNCTION dmbot_import_store(
        g BIGINT, c TEXT, imp TEXT, fname TEXT, started BIGINT, lines JSONB, terms INT,
        outside TEXT, confirmed JSONB
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
        IF terms < 6 OR jsonb_typeof(lines) IS DISTINCT FROM 'array'
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
                   'uploader_confirmed', who, now_s, terms, count(*)
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

    -- dmbot_import_members (0138's version), now also saying who refused (the page greys them out).
    CREATE OR REPLACE FUNCTION dmbot_import_members(
        g BIGINT, c TEXT, q TEXT, terms INT, outside TEXT
    ) RETURNS JSONB
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
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'userId', m.user_id::TEXT, 'name', m.name,
                       'refused', (SELECT r.kind FROM consent_refusals r
                                   WHERE r.guild_id = g AND r.user_id = m.user_id),
                       'agreed', EXISTS (SELECT 1 FROM consent x
                                         WHERE x.guild_id = g AND x.user_id = m.user_id
                                           AND x.terms_version = terms
                                           AND (outside IS NULL OR x.outside_to = outside)))
                       ORDER BY (lower(m.name) = lower(q)) DESC, m.rank, lower(m.name))
            FROM (SELECT one.name, one.user_id, one.rank FROM (
                      -- One row for each account: the campaign's name for the person if there is
                      -- one, else the directory's, else the website's. A search matches any of
                      -- the account's names, so it finds the same person whichever it hits.
                      SELECT n.user_id,
                             (array_agg(n.name ORDER BY n.rank, lower(n.name)))[1] AS name,
                             min(n.rank) AS rank,
                             lower(string_agg(n.hay, ' ')) AS hay
                      FROM (
                          SELECT e.played_by AS user_id, e.name, 0 AS rank, e.name AS hay
                          FROM memory_entities e
                          WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                            AND e.status = 'confirmed' AND e.played_by IS NOT NULL
                          UNION ALL
                          SELECT d.user_id,
                                 CASE WHEN d.display_name <> '' THEN d.display_name
                                      ELSE d.username END,
                                 1, d.display_name || ' ' || d.username
                          FROM member_directory d
                          WHERE d.guild_id = g AND (d.display_name <> '' OR d.username <> '')
                          UNION ALL
                          SELECT u.user_id, u.display_name, 2, u.display_name
                          FROM web_users u
                          WHERE coalesce(u.display_name, '') <> ''
                      ) n
                      WHERE EXISTS (SELECT 1 FROM consent x
                                    WHERE x.guild_id = g AND x.user_id = n.user_id
                                      AND x.terms_version = terms
                                      AND (outside IS NULL OR x.outside_to = outside))
                         OR n.rank = 0
                      GROUP BY n.user_id
                  ) one
                  WHERE q = '' OR position(lower(q) IN one.hay) > 0
                  ORDER BY (lower(one.name) = lower(q)) DESC, one.rank, lower(one.name)
                  LIMIT 25) m), '[]'::jsonb) INTO found;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN found;
    END
    $fn$;

    -- dmbot_import_speakers: the campaign's speakers without Discord and how many lines each has
    -- (the transcripts page lists them for the DMs' "remove this speaker's lines"). NULL unless a
    -- DM. Names and counts only.
    CREATE FUNCTION dmbot_import_speakers(g BIGINT, c TEXT) RETURNS JSONB
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
                       'id', s.user_id::TEXT, 'name', s.name,
                       'lines', (SELECT count(*) FROM transcript_lines l
                                 JOIN transcript_sessions t
                                   ON t.id = l.session_id AND t.guild_id = l.guild_id
                                 WHERE l.guild_id = g AND t.campaign_id = c
                                   AND l.user_id = s.user_id))
                       ORDER BY lower(s.name))
            FROM campaign_speakers s WHERE s.guild_id = g AND s.campaign_id = c), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_agreed(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        -- (CREATE OR REPLACE resets a function's settings, so its search_path is pinned again)
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_speakers(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_speaker(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_store('
            'BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_agreed(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_speaker(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_speakers(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_store(
        BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB) FROM PUBLIC;
    """
)

SQL = IMPORT_CONSENT

ISOLATED_TABLES: tuple[str, ...] = ("consent_refusals", "campaign_speakers", "import_consents")

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Importing with the uploader's consent per speaker (#3305): each is held to a DM of the
    # campaign.
    (
        "dmbot_import_store",
        "dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT, JSONB)",
    ),
    ("dmbot_import_speaker", "dmbot_import_speaker(BIGINT, TEXT, TEXT)"),
    ("dmbot_import_speakers", "dmbot_import_speakers(BIGINT, TEXT)"),
)
