"""0632_web_connection_reads

A connection said in the DM's own words, from the website (#2940 slice C).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# What Discord's ✍️ Correct it does (`ui/connection_say.py`), queued for the bot: the website has no
# AI key and its role cannot write the story graph, so the bot reads the words (`memory/
# connection_said.py`), keeps the proposals on the row, and writes what the DM confirmed, with the
# same calls as Discord. One row is one read and what follows it: `job` says what waits next
# ('read', then after the DM's Use this 'use', and after an Undo 'undo'), `state` where that stands.
#
# The DM's own words sit in the row only while the read waits or runs (`words`): the bot blanks them
# in the same UPDATE that ends the read, and the table refuses a finished read that keeps them. The
# proposals and the names left out (also the DM's words) go the same way when the row ends. Rows
# live a day and go with their campaign; only the DM who asked reads theirs back. Counts, stages
# and codes are all that the page is told besides the proposals' own fixed words.
SQL = (
    r"""
    CREATE TABLE web_connection_reads (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        first_id    TEXT NOT NULL,
        second_id   TEXT NOT NULL,
        -- The connection being corrected, as the DM saw it: its id and a hash of its fields
        -- (`basis`), so the bot refuses a connection that changed since.
        replacing_id TEXT,
        basis       TEXT NOT NULL DEFAULT '' CHECK (basis = '' OR basis ~ '^[0-9a-f]{32}$'),
        words       TEXT NOT NULL DEFAULT '' CHECK (char_length(words) <= 1000),
        job         TEXT NOT NULL DEFAULT 'read' CHECK (job IN ('read', 'use', 'undo')),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'ready', 'done', 'undone', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        -- What the bot read: the connections in fixed fields, who knows them, the characters named
        -- (ids) and the names it could not place (the DM's own words, blanked when the row ends).
        proposals   JSONB NOT NULL DEFAULT '[]'::jsonb
            CHECK (jsonb_typeof(proposals) = 'array' AND pg_column_size(proposals) <= 20000),
        who_kind    TEXT NOT NULL DEFAULT 'everyone'
            CHECK (who_kind IN ('everyone', 'dm', 'some')),
        characters  TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(characters) <= 10),
        left_out    TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(left_out) <= 10),
        -- The DM's Use this: which proposals (their numbers, from 1), who knows, and which
        -- characters.
        picks       INT[] NOT NULL DEFAULT '{}' CHECK (cardinality(picks) <= 10),
        use_who     TEXT NOT NULL DEFAULT ''
            CHECK (use_who IN ('', 'everyone', 'dm', 'some', 'keep')),
        use_chars   TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(use_chars) <= 10),
        -- What Use this did, for Undo: the change batches (none for nothing to take back).
        batches     BIGINT[] NOT NULL DEFAULT '{}' CHECK (cardinality(batches) <= 40),
        partly      BOOLEAN NOT NULL DEFAULT FALSE,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((job = 'read' AND state IN ('waiting', 'working')) OR words = ''),
        CHECK (state NOT IN ('done', 'undone', 'failed')
               OR (proposals = '[]'::jsonb AND cardinality(left_out) = 0))
    );
    CREATE INDEX web_connection_reads_open ON web_connection_reads (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_connection_reads")
    + r"""
    -- Ask for a read of the DM's words about two names. Answers 'ok' with the id, and refuses with:
    -- 'bad_text' (empty or over 1,000), 'bad_choice' (the two are one name, or the connection
    -- to correct does not join them), 'busy' (a read of this campaign is waiting or running)
    -- and 'hour_full' (ten reads in the last hour: the AI is paid for by the DM). NULL for
    -- anyone who is not a DM of the campaign, a server the caller is not in, and names that
    -- are not confirmed named entries of the campaign. Reads that the AI never answered are
    -- not counted.
    CREATE FUNCTION dmbot_web_connection_read_add(
        g BIGINT, c TEXT, one TEXT, two TEXT, old TEXT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        want TEXT := btrim(regexp_replace(COALESCE(txt, ''), '\s+', ' ', 'g'));
        mark TEXT := '';
    BEGIN
        IF me IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me)
           OR (SELECT count(*) FROM memory_entities e
               WHERE e.guild_id = g AND e.campaign_id = c AND e.id IN (one, two)
                 AND e.status = 'confirmed' AND e.merged_into IS NULL AND NOT e.unnamed
                 AND e.type <> 'person') <> 2 THEN
            RETURN NULL;
        END IF;
        IF one = two THEN
            RETURN jsonb_build_object('status', 'bad_choice');
        END IF;
        IF char_length(want) NOT BETWEEN 1 AND 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF old IS NOT NULL THEN
            SELECT md5(concat_ws('|', r.predicate, r.detail, r.subject_id, r.object_id,
                                 r.secret::TEXT, r.unrevealed::TEXT, r.directional::TEXT))
                INTO mark
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.id = old
                  AND r.status = 'confirmed' AND r.to_session_at IS NULL
                  AND ((r.subject_id = one AND r.object_id = two)
                       OR (r.subject_id = two AND r.object_id = one));
            IF mark IS NULL THEN
                RETURN jsonb_build_object('status', 'bad_choice');
            END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('connection_read:' || g::TEXT || c, 0));
        DELETE FROM web_connection_reads f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.created_at < now_s - 86400;
        -- A read the bot took and then went quiet on for ten minutes goes back to waiting.
        UPDATE web_connection_reads f SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE f.guild_id = g AND f.campaign_id = c AND f.state = 'working'
              AND f.updated_at < now_s - 600;
        IF EXISTS (SELECT 1 FROM web_connection_reads f
                   WHERE f.guild_id = g AND f.campaign_id = c AND f.job = 'read'
                     AND f.state IN ('waiting', 'working')) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM web_connection_reads f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.created_at >= now_s - 3600
              AND NOT (f.state = 'failed'
                       AND f.error IN ('unreadable', 'not_available'))) >= 10 THEN
            RETURN jsonb_build_object('status', 'hour_full');
        END IF;
        INSERT INTO web_connection_reads (guild_id, campaign_id, id, user_id, first_id, second_id,
                                          replacing_id, basis, words, created_at, updated_at)
            VALUES (g, c, rid, me, one, two, old, mark, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one of the caller's own reads stands: the job, the state and the plain error word, the
    -- proposals with the entries' names (fixed fields, never the DM's sentence), who knows them,
    -- the characters named and the names that could not be placed, and whether Use this can be
    -- taken back. NULL when it is not the caller's in this campaign, or is over a day old.
    CREATE FUNCTION dmbot_web_connection_read_get(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_connection_reads%ROWTYPE;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
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
            'replacing', r.replacing_id,
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

    -- The DM's Use this: which proposals (their numbers from 1), who knows them ('everyone', 'dm',
    -- 'some' with up to ten characters, or 'keep' for a correction to keep who saw the old one).
    -- Answers 'ok', 'not_ready' (the read is not waiting for an answer) or 'bad_choice'. NULL for
    -- anyone but the DM who asked.
    CREATE FUNCTION dmbot_web_connection_use(
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
           OR (who = 'some' AND cardinality(chars) = 0)
           OR (who <> 'some' AND cardinality(chars) <> 0) THEN
            RETURN jsonb_build_object('status', 'bad_choice');
        END IF;
        UPDATE web_connection_reads SET job = 'use', state = 'waiting', claimed_at = 0,
            picks = pick, use_who = who, use_chars = chars, updated_at = now_s
            WHERE guild_id = g AND id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Take back what Use this did: only the DM who asked, only a use that is done and wrote
    -- something. Answers 'ok' or 'not_ready'; NULL for anyone else.
    CREATE FUNCTION dmbot_web_connection_undo(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
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
            updated_at = now_s
            WHERE guild_id = g AND id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_connection_read_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
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
    REVOKE ALL ON FUNCTION dmbot_web_connection_read_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_connection_read_get(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_connection_use(BIGINT, TEXT, TEXT, INT[], TEXT, TEXT[])
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_connection_undo(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("web_connection_reads",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_connection_read_add",
        "dmbot_web_connection_read_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)",
    ),
    ("dmbot_web_connection_read_get", "dmbot_web_connection_read_get(BIGINT, TEXT, TEXT)"),
    (
        "dmbot_web_connection_use",
        "dmbot_web_connection_use(BIGINT, TEXT, TEXT, INT[], TEXT, TEXT[])",
    ),
    ("dmbot_web_connection_undo", "dmbot_web_connection_undo(BIGINT, TEXT, TEXT)"),
)
