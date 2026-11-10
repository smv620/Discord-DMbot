"""0411_trait_answers

The website answers a lineage or class question (#3946, the web half of #3926): "Yes, change it" or
"No, leave it" is queued for the bot, which applies it with `TraitOfferStore.keep` or `never`.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The page never writes a campaign's story itself: a press is a row the bot answers, like Hear it
# (0387) and Make the script (0401). The row holds ids, the answer and counts only: never the
# trait's words or the quote. One open answer for a question at a time (a partial unique index),
# so a second press or a second tab is given the first one's id. `batch` is the change a Yes made,
# for the page's Undo; the bot writes it. `claimed_at` is the bot's own stamp in milliseconds,
# which it moves as it works (the heartbeat), as 0407 reads it; `created_at` is in seconds. A
# press waiting or working for 30 minutes without moving is taken as lost, and rows go after a
# day. The door checks the caller is a DM of the campaign and that the question is really waiting;
# the bot checks again when it applies it.
SQL = (
    r"""
    CREATE TABLE trait_answers (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        entity_id   TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('species', 'class')),
        name_key    TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 100),
        action      TEXT NOT NULL CHECK (action IN ('keep', 'never')),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        batch       BIGINT,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        tries       INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX trait_answers_claim ON trait_answers (guild_id, state, created_at);
    CREATE UNIQUE INDEX trait_answers_one_open
        ON trait_answers (guild_id, campaign_id, entity_id, kind, name_key)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("trait_answers")
    + r"""
    -- Queue one answer (NULL unless the caller is a DM of the campaign). 'not_found' when no such
    -- question is waiting; the id of the answer already going when there is one; 'too_soon' past 60
    -- answers a minute for the campaign.
    CREATE FUNCTION dmbot_web_trait_answer_add(
        g BIGINT, c TEXT, ent TEXT, k TEXT, nk TEXT, act TEXT) RETURNS JSONB
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
        IF act NOT IN ('keep', 'never') OR k NOT IN ('species', 'class') THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('trait_answer:' || g::TEXT || ':' || c, 0));
        DELETE FROM trait_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 86400;
        UPDATE trait_answers a SET state = 'failed', error = 'lost'
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')
              AND GREATEST(a.created_at, a.claimed_at / 1000) < now_s - 1800;
        IF NOT EXISTS (SELECT 1 FROM memory_trait_offers o
                       WHERE o.guild_id = g AND o.campaign_id = c AND o.entity_id = ent
                         AND o.kind = k AND o.name_key = nk AND o.state = 'waiting') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT a.id INTO running FROM trait_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent AND a.kind = k
              AND a.name_key = nk AND a.state IN ('waiting', 'working');
        IF running IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', running);
        END IF;
        IF (SELECT count(*) FROM trait_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at >= now_s - 60) >= 60 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO trait_answers (guild_id, campaign_id, id, user_id, entity_id, kind, name_key,
                                   action, created_at)
            VALUES (g, c, rid, dmbot_current_user(), ent, k, nk, act, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one answer stands, for any DM of the campaign; NULL when there is none such.
    CREATE FUNCTION dmbot_web_trait_answer_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r trait_answers%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM trait_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'state', r.state, 'error', r.error, 'action', r.action, 'batch', r.batch,
            'createdAt', r.created_at, 'claimedAt', r.claimed_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_trait_answer_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_trait_answer_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_trait_answer_add(
        BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_trait_answer_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("trait_answers",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_trait_answer_add",
        "dmbot_web_trait_answer_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)",
    ),
    ("dmbot_web_trait_answer_read", "dmbot_web_trait_answer_read(BIGINT, TEXT, TEXT)"),
)
