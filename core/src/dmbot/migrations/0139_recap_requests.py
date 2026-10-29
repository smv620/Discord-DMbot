"""0139_recap_requests

The recap page's notes to DMbot (#2113).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RECAP_REQUESTS = (
    f"""
    -- What a DM types on the recap page to fix a recap (#2113). The website has no AI key, so it
    -- only queues the words here; the bot sorts them (the same sort as the DM screen's) and
    -- writes the points back on this row, which the page polls. The words are the DM's own about
    -- one recap and live here for an hour at most; they are never logged. `answer` holds the
    -- sorted points. Per campaign, isolated, gone with the campaign, left out of backups.
    CREATE TABLE recap_requests (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        session_at  BIGINT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        kind        TEXT NOT NULL CHECK (kind IN ('instructions')),
        body        TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 1000),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        answer      JSONB,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX recap_requests_open ON recap_requests (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("recap_requests")
    + r"""
    -- The doors the website's role uses: it has no grant on the table. Each checks the caller is
    -- a DM of the campaign in a server on the session's list.
    CREATE FUNCTION dmbot_recap_request_add(g BIGINT, c TEXT, at_ BIGINT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF txt IS NULL OR char_length(btrim(txt)) = 0 OR char_length(txt) > 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs j
                       WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                         AND j.state = 'ready' AND j.status = 'draft') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_request:' || g::TEXT || c, 0));
        -- The one-hour promise holds for a server the bot has left too: old words go here.
        DELETE FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 3600;
        IF (SELECT count(*) FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')
              AND r.created_at > now_s - 600) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,
                                    created_at)
            VALUES (g, c, rid, at_, dmbot_current_user(), 'instructions', btrim(txt), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- A request's state and sorted points, to the DM who made it and no one else.
    CREATE FUNCTION dmbot_recap_request_read(g BIGINT, c TEXT, at_ BIGINT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r recap_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'answer', r.answer);
    END
    $fn$;

    -- "Rewrite with these": the points the DM kept go to the recap's next try (corrections, for
    -- this recap only) and to the campaign's standing notes (style, at most 10, the oldest
    -- dropped), the recap goes back to the queue with one more try, and the approval ends.
    -- Only a request of the caller's own that is done; ids must be ones it offered.
    CREATE FUNCTION dmbot_recap_rewrite(
        g BIGINT, c TEXT, at_ BIGINT, rid TEXT, keep_c TEXT[], keep_s TEXT[]
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        -- The caps are the code's own (recaps.store MAX_TRIES and MAX_CORRECTIONS, recaps.style
        -- MAX_NOTES; a test keeps them equal): a caller of this door can't choose them.
        max_tries CONSTANT INT := 2;
        max_corrections CONSTANT INT := 10;
        max_notes CONSTANT INT := 10;
        r recap_requests%ROWTYPE;
        job recap_jobs%ROWTYPE;
        have TEXT[];
        add_c TEXT[] := '{}';
        add_s TEXT[] := '{}';
        item TEXT;
        pick JSONB;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        top INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- The campaign row first, the same order as the Discord notes path, so the two can't
        -- wait on each other.
        PERFORM 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c FOR UPDATE;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.user_id = dmbot_current_user() AND x.state = 'done' FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        FOREACH item IN ARRAY coalesce(keep_c, '{}') LOOP
            SELECT p->>'text' INTO item FROM jsonb_array_elements(r.answer->'corrections') p
                WHERE p->>'id' = item;
            IF item IS NULL THEN
                RETURN jsonb_build_object('status', 'bad_choice');
            END IF;
            add_c := add_c || item;
        END LOOP;
        FOREACH item IN ARRAY coalesce(keep_s, '{}') LOOP
            SELECT p->>'text' INTO item FROM jsonb_array_elements(r.answer->'style') p
                WHERE p->>'id' = item;
            IF item IS NULL THEN
                RETURN jsonb_build_object('status', 'bad_choice');
            END IF;
            add_s := add_s || item;
        END LOOP;
        IF cardinality(add_c) + cardinality(add_s) = 0 THEN
            RETURN jsonb_build_object('status', 'bad_choice');
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.status <> 'draft' OR job.state NOT IN ('ready', 'failed') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        IF job.tries >= max_tries THEN
            RETURN jsonb_build_object('status', 'no_tries');
        END IF;
        have := job.corrections;
        FOREACH item IN ARRAY add_c LOOP
            IF NOT item = ANY (have) THEN
                have := have || item;
            END IF;
        END LOOP;
        IF cardinality(have) > max_corrections THEN
            have := have[cardinality(have) - max_corrections + 1 : cardinality(have)];
        END IF;
        FOREACH item IN ARRAY add_s LOOP
            item := regexp_replace(btrim(left(item, 300)), '\s+', ' ', 'g');  -- as NoteStore.add
            DELETE FROM recap_style_notes n
                WHERE n.guild_id = g AND n.campaign_id = c AND lower(n.text) = lower(item);
            DELETE FROM recap_style_notes n
                WHERE n.guild_id = g AND n.campaign_id = c AND n.id IN (
                    SELECT o.id FROM recap_style_notes o
                    WHERE o.guild_id = g AND o.campaign_id = c
                    ORDER BY o.position, o.id
                    LIMIT greatest(0, (SELECT count(*) FROM recap_style_notes q
                                       WHERE q.guild_id = g AND q.campaign_id = c)
                                      - max_notes + 1));
            SELECT coalesce(max(position), 0) + 1 INTO top FROM recap_style_notes n
                WHERE n.guild_id = g AND n.campaign_id = c;
            INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,
                                           created_at)
                VALUES (g, c, top, item, dmbot_current_user(), now_s);
        END LOOP;
        UPDATE recap_jobs SET corrections = have, tries = tries + 1, state = 'queued',
            approved_by = 0, approved_at = 0, approved_hash = '', audio_choice = ''
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        DELETE FROM recap_requests WHERE guild_id = g AND id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_rewrite(BIGINT, TEXT, BIGINT, TEXT, TEXT[], TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_rewrite(
        BIGINT, TEXT, BIGINT, TEXT, TEXT[], TEXT[]) FROM PUBLIC;
    """
)

SQL = RECAP_REQUESTS

ISOLATED_TABLES: tuple[str, ...] = ("recap_requests",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Notes typed on the recap page and "Rewrite with these" (#2113).
    ("dmbot_recap_request_add", "dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT)"),
    ("dmbot_recap_request_read", "dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT)"),
    (
        "dmbot_recap_rewrite",
        "dmbot_recap_rewrite(BIGINT, TEXT, BIGINT, TEXT, TEXT[], TEXT[])",
    ),
)
