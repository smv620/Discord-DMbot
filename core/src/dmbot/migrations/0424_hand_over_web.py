"""0424_hand_over_web

The "Does Claude still have it?" questions on the website (#4122, the web half of #4115 slice 1):
the bot keeps a list of the questions waiting, the page reads it and queues the DM's answers, and
the bot applies them with `MemoryStore.answer_hand_over`, as the Discord card does.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Why a list the bot writes, not a door that works the questions out: the rule is Python's
# (`memory/hand_over.waiting`: one of a kind is partly a guess from the name, and a hidden tie is
# never asked about against a seen one), and one rule must decide both places. The bot rewrites a
# campaign's rows each minute (`hand_over_answer.refresh`); the door shows only rows whose two ties
# are still current, and the bot checks again before it applies an answer. Ids and a time only:
# the door adds the names for the DM. Not in a backup: made again within a minute.
#
# The page never writes the story itself: a press is a row in `hand_over_answers` the bot answers,
# like `trait_answers` (0411): ids, the answer and the state, never a word. `no` ends the older
# holder's tie (its `batch` is the change, for Undo); `both` is remembered; `undo` takes back an
# earlier `no` (`undoes` is that answer's id). One open answer for a question at a time; a press
# waiting or working for 30 minutes without moving is taken as lost, and rows go after a day.
SQL = (
    r"""
    CREATE TABLE hand_over_questions (
        guild_id            BIGINT NOT NULL,
        campaign_id         TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        item_id             TEXT NOT NULL,
        older_id            TEXT NOT NULL,
        newer_id            TEXT NOT NULL,
        predicate           TEXT NOT NULL CHECK (predicate IN ('owns', 'carries')),
        older_relation_id   TEXT NOT NULL,
        newer_relation_id   TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, older_relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, newer_relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        place               INT NOT NULL CHECK (place >= 0),  -- the order the bot asks them in
        made_at             BIGINT NOT NULL CHECK (made_at > 0),
        PRIMARY KEY (guild_id, campaign_id, older_relation_id, newer_relation_id)
    );
    -- (a deleted connection finds its rows by index: the newer end of each key)
    CREATE INDEX hand_over_questions_newer
        ON hand_over_questions (guild_id, campaign_id, newer_relation_id);

    CREATE TABLE hand_over_answers (
        guild_id            BIGINT NOT NULL,
        campaign_id         TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id                  TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id             BIGINT NOT NULL CHECK (user_id > 0),
        older_relation_id   TEXT NOT NULL CHECK (older_relation_id ~ '^[0-9a-f]{32}$'),
        newer_relation_id   TEXT NOT NULL CHECK (newer_relation_id ~ '^[0-9a-f]{32}$'),
        action              TEXT NOT NULL CHECK (action IN ('no', 'both', 'undo')),
        undoes              TEXT CHECK (undoes ~ '^[0-9a-f]{32}$'),
        state               TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error               TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        batch               BIGINT,
        created_at          BIGINT NOT NULL,
        claimed_at          BIGINT NOT NULL DEFAULT 0,
        tries               INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        CHECK ((action = 'undo') = (undoes IS NOT NULL))
    );
    CREATE INDEX hand_over_answers_claim ON hand_over_answers (guild_id, state, created_at);
    CREATE UNIQUE INDEX hand_over_answers_one_open
        ON hand_over_answers (guild_id, campaign_id, older_relation_id, newer_relation_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("hand_over_questions")
    + _isolate("hand_over_answers")
    + r"""
    -- The questions waiting for the DM, oldest first (NULL unless the caller is a DM of the
    -- campaign): the item, who had it first, who has it now, the kind of tie and the two ties' ids,
    -- with the names the DM sees. Only rows whose two ties are both still current and confirmed.
    CREATE FUNCTION dmbot_web_hand_over_questions(g BIGINT, c TEXT) RETURNS JSONB
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
                'item', q.item_id, 'itemName', i.name,
                'older', q.older_id, 'olderName', a.name,
                'newer', q.newer_id, 'newerName', b.name,
                'predicate', q.predicate,
                'olderTie', q.older_relation_id, 'newerTie', q.newer_relation_id,
                'madeAt', q.made_at) ORDER BY q.place)
            FROM hand_over_questions q
            JOIN memory_entities i ON i.guild_id = q.guild_id AND i.campaign_id = q.campaign_id
                AND i.id = q.item_id
            JOIN memory_entities a ON a.guild_id = q.guild_id AND a.campaign_id = q.campaign_id
                AND a.id = q.older_id
            JOIN memory_entities b ON b.guild_id = q.guild_id AND b.campaign_id = q.campaign_id
                AND b.id = q.newer_id
            WHERE q.guild_id = g AND q.campaign_id = c
              AND EXISTS (SELECT 1 FROM memory_relations r
                          WHERE r.guild_id = g AND r.campaign_id = c
                            AND r.id IN (q.older_relation_id, q.newer_relation_id)
                            AND r.status = 'confirmed' AND r.to_session_at IS NULL
                          HAVING count(*) = 2)), '[]'::JSONB);
    END
    $fn$;

    -- Queue one answer (NULL unless the caller is a DM of the campaign). `no` or `both` for a
    -- question in the list; `undo` with `u` the id of an earlier `no` that is done. 'not_found'
    -- when there is no such question or answer; the id of the answer already going when there is
    -- one; 'too_soon' past 60 answers a minute for the campaign.
    CREATE FUNCTION dmbot_web_hand_over_answer_add(
        g BIGINT, c TEXT, older TEXT, newer TEXT, act TEXT, u TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        running TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act NOT IN ('no', 'both', 'undo') OR older !~ '^[0-9a-f]{32}$'
           OR newer !~ '^[0-9a-f]{32}$' OR ((act = 'undo') <> (u IS NOT NULL))
           OR (u IS NOT NULL AND u !~ '^[0-9a-f]{32}$') THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('hand_over_answer:' || g::TEXT || ':' || c, 0));
        DELETE FROM hand_over_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 86400;
        UPDATE hand_over_answers a SET state = 'failed', error = 'lost'
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')
              AND GREATEST(a.created_at, a.claimed_at / 1000) < now_s - 1800;
        IF act = 'undo' THEN
            IF NOT EXISTS (SELECT 1 FROM hand_over_answers a
                           WHERE a.guild_id = g AND a.campaign_id = c AND a.id = u
                             AND a.action = 'no' AND a.state = 'done' AND a.batch IS NOT NULL
                             AND a.older_relation_id = older AND a.newer_relation_id = newer) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM hand_over_questions q
                          WHERE q.guild_id = g AND q.campaign_id = c
                            AND q.older_relation_id = older AND q.newer_relation_id = newer) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT a.id INTO running FROM hand_over_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.older_relation_id = older
              AND a.newer_relation_id = newer AND a.state IN ('waiting', 'working');
        IF running IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', running);
        END IF;
        IF (SELECT count(*) FROM hand_over_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at >= now_s - 60) >= 60 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO hand_over_answers (guild_id, campaign_id, id, user_id, older_relation_id,
                                       newer_relation_id, action, undoes, created_at)
            VALUES (g, c, rid, dmbot_current_user(), older, newer, act, u, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one answer stands, for any DM of the campaign; NULL when there is none such.
    CREATE FUNCTION dmbot_web_hand_over_answer_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r hand_over_answers%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM hand_over_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'state', r.state, 'error', r.error, 'action', r.action,
            'undoable', r.action = 'no' AND r.state = 'done' AND r.batch IS NOT NULL,
            'createdAt', r.created_at, 'claimedAt', r.claimed_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_hand_over_questions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_hand_over_answer_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_hand_over_answer_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_hand_over_questions(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_hand_over_answer_add(
        BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_hand_over_answer_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("hand_over_questions", "hand_over_answers")

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_hand_over_questions", "dmbot_web_hand_over_questions(BIGINT, TEXT)"),
    (
        "dmbot_web_hand_over_answer_add",
        "dmbot_web_hand_over_answer_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)",
    ),
    ("dmbot_web_hand_over_answer_read", "dmbot_web_hand_over_answer_read(BIGINT, TEXT, TEXT)"),
)
