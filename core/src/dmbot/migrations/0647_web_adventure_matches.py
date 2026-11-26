"""0647_web_adventure_matches

Match to files for a Shared story adventure, on the website (#3694, slice G1).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# "Same as in the book" for the DM's table names, asked for on the website and done by the bot with
# the calls Discord's 🔗 Match to files makes (`memory.book_links`: `suggestions`, `decide`, and
# `memory.undo` of what `decide` wrote). The website's role cannot read or write the story memory,
# so a job is queued and the bot answers. Jobs: `suggest` (the pairs worth asking about), `decide`
# (the DM's Same or Not the same for up to ten pairs; each is its own change, so each has its own
# Undo) and `undo` (take back the changes of an earlier `decide` job, newest first).
#
# `picks` and `answer` hold ids and names (the book's and the table's), so they are for a DM of that
# one campaign only, never logged, and kept for a day at most. The table refuses an answer on any
# row that is not finished. It goes with its campaign and with the person who asked (0648), and
# is left out of backups.
SQL = (
    r"""
    CREATE TABLE web_adventure_matches (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        job         TEXT NOT NULL CHECK (job IN ('suggest', 'decide', 'undo')),
        -- decide: [[plan id, table entry id, 'same' | 'not_same'], ...], at most ten.
        picks       JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(picks) = 'array'
                        AND jsonb_array_length(picks) <= 10),
        -- undo: the decide job whose changes are taken back, and (set by the bot when a decide
        -- job is done, copied by the door into the undo job) the changes' batch numbers.
        source_id   TEXT NOT NULL DEFAULT '' CHECK (source_id ~ '^([0-9a-f]{32})?$'),
        batches     BIGINT[] NOT NULL DEFAULT '{}' CHECK (cardinality(batches) <= 10),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        answer      JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(answer) <= 60000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state = 'failed') = (error <> '')),
        CHECK (state = 'done' OR answer = '{}'::jsonb),
        CHECK ((job = 'decide') = (jsonb_array_length(picks) > 0)),
        CHECK ((job = 'undo') = (source_id <> ''))
    );
    CREATE INDEX web_adventure_matches_open ON web_adventure_matches (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_adventure_matches_one_open
        ON web_adventure_matches (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_adventure_matches")
    + r"""
    -- Queue a job, for a DM of the campaign in a server on the caller's list (NULL for anyone
    -- else). 'not_on': the Shared story switch is Off or no DM confirmed the right to use the
    -- material. 'bad_job': an unknown job, or picks that are not up to ten of [32 hex, 32 hex,
    -- 'same' | 'not_same'], or an undo with no source. 'nothing_to_undo': the source is not a
    -- finished decide job of this person that wrote something, or it was already undone. 'busy':
    -- another job is open (its id comes back). 'too_soon': sixty in an hour. 'ok' with the id
    -- otherwise. The bot checks all of it again.
    CREATE FUNCTION dmbot_web_adventure_match(
        g BIGINT, c TEXT, kind TEXT, chosen JSONB, src TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        picks_in JSONB := COALESCE(chosen, '[]'::jsonb);
        source_in TEXT := COALESCE(src, '');
        batch_list BIGINT[] := '{}';
        item JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = me) THEN
            RETURN NULL;
        END IF;
        IF kind IS NULL OR kind NOT IN ('suggest', 'decide', 'undo')
           OR jsonb_typeof(picks_in) <> 'array' OR jsonb_array_length(picks_in) > 10
           OR (kind = 'decide') <> (jsonb_array_length(picks_in) > 0)
           OR (kind = 'undo') <> (source_in <> '')
           OR source_in !~ '^([0-9a-f]{32})?$' THEN
            RETURN jsonb_build_object('status', 'bad_job');
        END IF;
        FOR item IN SELECT * FROM jsonb_array_elements(picks_in) LOOP
            IF jsonb_typeof(item) <> 'array' OR jsonb_array_length(item) <> 3
               OR jsonb_typeof(item -> 0) <> 'string' OR jsonb_typeof(item -> 1) <> 'string'
               OR jsonb_typeof(item -> 2) <> 'string'
               OR (item ->> 0) !~ '^[0-9a-f]{32}$' OR (item ->> 1) !~ '^[0-9a-f]{32}$'
               OR (item ->> 2) NOT IN ('same', 'not_same') THEN
                RETURN jsonb_build_object('status', 'bad_job');
            END IF;
        END LOOP;
        IF NOT EXISTS (SELECT 1 FROM campaigns k
                       WHERE k.guild_id = g AND k.id = c AND k.story_mode <> 'off')
           OR NOT EXISTS (SELECT 1 FROM shared_confirmations s
                          WHERE s.guild_id = g AND s.campaign_id = c
                            AND s.purpose = 'shared_story') THEN
            RETURN jsonb_build_object('status', 'not_on');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('adventure_match:' || g::TEXT || c, 0));
        DELETE FROM web_adventure_matches r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        UPDATE web_adventure_matches r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_adventure_matches r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF kind = 'undo' THEN
            SELECT r.batches INTO batch_list FROM web_adventure_matches r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.id = source_in
                  AND r.user_id = me AND r.job = 'decide' AND r.state = 'done'
                  AND cardinality(r.batches) > 0;
            IF NOT FOUND OR EXISTS (SELECT 1 FROM web_adventure_matches u
                                    WHERE u.guild_id = g AND u.campaign_id = c
                                      AND u.job = 'undo' AND u.source_id = source_in
                                      AND u.state IN ('waiting', 'working', 'done')) THEN
                RETURN jsonb_build_object('status', 'nothing_to_undo');
            END IF;
        END IF;
        IF (SELECT count(*) FROM web_adventure_matches r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 60 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_adventure_matches (guild_id, campaign_id, id, user_id, job, picks,
                                           source_id, batches, created_at, updated_at)
            VALUES (g, c, rid, me, kind, picks_in, source_in, batch_list, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- One job's state, and its answer once done, for the DM of the campaign who asked (ids and
    -- names from the book and the table: NULL for anyone else, 'missing' for a row that is gone).
    CREATE FUNCTION dmbot_web_adventure_match_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_adventure_matches%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_adventure_matches q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = rid;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('id', r.id, 'job', r.job, 'state', r.state, 'error', r.error,
            'answer', r.answer, 'canUndo', r.job = 'decide' AND r.state = 'done'
                AND cardinality(r.batches) > 0,
            'createdAt', r.created_at, 'updatedAt', r.updated_at,
            'serverNow', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_match(BIGINT, TEXT, TEXT, JSONB, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_match_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_match(BIGINT, TEXT, TEXT, JSONB, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_match_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("web_adventure_matches",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_adventure_match",
        "dmbot_web_adventure_match(BIGINT, TEXT, TEXT, JSONB, TEXT)",
    ),
    ("dmbot_web_adventure_match_read", "dmbot_web_adventure_match_read(BIGINT, TEXT, TEXT)"),
)
