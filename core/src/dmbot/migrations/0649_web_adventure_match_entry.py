"""0649_web_adventure_match_entry

Match to files for one table entry, on the website (#3694, slice G2).
"""

from __future__ import annotations

# One table entry's pick, the website's half of Discord's 🔗 "Same as in the book…" (a name's
# card): the book entries it could be (`options`: `book_links.options_for` and `linked`, 25 a
# page), the DM's word that it is one of them (`pick`: `book_links.decide` of one pair as Same)
# and taking that word off (`unpick`: `book_links.unlink`). They are three more jobs of the slice
# G1 queue (`web_adventure_matches`, 0647), done by the bot with the calls Discord makes;
# `book_links` itself is not changed. A pick and an unpick are each one change with its own Undo,
# which the `undo` job of G1 takes back, so this migration also lets an undo name them as its
# source (the doors are replaced whole, search path set again, as a replaced function loses it).
#
# The same rules as G1: the answer holds ids and names from the book and the table, so it is for
# a DM of the campaign only, never logged, a day at most, and the table refuses it on a row that
# is not finished.
SQL = r"""
    -- The job and its columns. The checks on `job` are found by what they say and replaced: their
    -- names were made by the database.
    DO $do$
    DECLARE
        found RECORD;
    BEGIN
        FOR found IN
            SELECT c.conname FROM pg_constraint c
            WHERE c.conrelid = 'web_adventure_matches'::regclass AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) LIKE '%job%'
        LOOP
            EXECUTE format('ALTER TABLE web_adventure_matches DROP CONSTRAINT %I', found.conname);
        END LOOP;
    END
    $do$;
    ALTER TABLE web_adventure_matches
        ADD COLUMN entity_id TEXT NOT NULL DEFAULT '' CHECK (entity_id ~ '^([0-9a-f]{32})?$'),
        ADD COLUMN plan_id   TEXT NOT NULL DEFAULT '' CHECK (plan_id ~ '^([0-9a-f]{32})?$'),
        ADD COLUMN skip      INT  NOT NULL DEFAULT 0 CHECK (skip >= 0);
    ALTER TABLE web_adventure_matches
        ADD CONSTRAINT web_adventure_matches_job
            CHECK (job IN ('suggest', 'decide', 'undo', 'options', 'pick', 'unpick')),
        ADD CONSTRAINT web_adventure_matches_decide
            CHECK ((job = 'decide') = (jsonb_array_length(picks) > 0)),
        ADD CONSTRAINT web_adventure_matches_undo CHECK ((job = 'undo') = (source_id <> '')),
        ADD CONSTRAINT web_adventure_matches_entry
            CHECK ((job IN ('options', 'pick', 'unpick')) = (entity_id <> '')),
        ADD CONSTRAINT web_adventure_matches_plan
            CHECK ((job IN ('pick', 'unpick')) = (plan_id <> ''));

    -- Queue one of the three jobs for one table entry, for a DM of the campaign in a server on the
    -- caller's list (NULL for anyone else). `kind` is `options` (with `skip_n`, the page to start
    -- at), `pick` or `unpick` (with `pln`, the book entry). 'bad_job': an unknown job, an id that
    -- is not 32 hex characters, a plan on `options` or a page on a write. 'not_on', 'busy' and
    -- 'too_soon' as for the others. 'ok' with the id otherwise. The bot checks it all again.
    CREATE FUNCTION dmbot_web_adventure_match_entry(
        g BIGINT, c TEXT, kind TEXT, ent TEXT, pln TEXT, skip_n INT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        plan_in TEXT := COALESCE(pln, '');
        skip_in INT := COALESCE(skip_n, 0);
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = me) THEN
            RETURN NULL;
        END IF;
        IF kind IS NULL OR kind NOT IN ('options', 'pick', 'unpick')
           OR COALESCE(ent, '') !~ '^[0-9a-f]{32}$' OR plan_in !~ '^([0-9a-f]{32})?$'
           OR (kind = 'options') <> (plan_in = '')
           OR (kind <> 'options' AND skip_in <> 0) OR skip_in < 0 OR skip_in > 100000 THEN
            RETURN jsonb_build_object('status', 'bad_job');
        END IF;
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
        IF (SELECT count(*) FROM web_adventure_matches r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 60 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_adventure_matches (guild_id, campaign_id, id, user_id, job, entity_id,
                                           plan_id, skip, created_at, updated_at)
            VALUES (g, c, rid, me, kind, ent, plan_in, skip_in, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Queue a job, for a DM of the campaign in a server on the caller's list (NULL for anyone
    -- else). 'not_on': the Shared story switch is Off or no DM confirmed the right to use the
    -- material. 'bad_job': an unknown job, or picks that are not up to ten of [32 hex, 32 hex,
    -- 'same' | 'not_same'], or an undo with no source. 'nothing_to_undo': the source is not a
    -- finished decide job of this person that wrote something, or it was already undone. 'busy':
    -- another job is open (its id comes back). 'too_soon': sixty in an hour. 'ok' with the id
    -- otherwise. The bot checks all of it again.
    CREATE OR REPLACE FUNCTION dmbot_web_adventure_match(
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
                  AND r.user_id = me AND r.job IN ('decide', 'pick', 'unpick') AND r.state = 'done'
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
    CREATE OR REPLACE FUNCTION dmbot_web_adventure_match_read(
        g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
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
            'answer', r.answer,
            'canUndo', r.job IN ('decide', 'pick', 'unpick') AND r.state = 'done'
                AND cardinality(r.batches) > 0,
            'createdAt', r.created_at, 'updatedAt', r.updated_at,
            'serverNow', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_match_entry(BIGINT, TEXT, TEXT, TEXT, TEXT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_match(BIGINT, TEXT, TEXT, JSONB, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_match_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_match_entry(
        BIGINT, TEXT, TEXT, TEXT, TEXT, INT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_adventure_match_entry",
        "dmbot_web_adventure_match_entry(BIGINT, TEXT, TEXT, TEXT, TEXT, INT)",
    ),
)
