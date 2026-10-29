"""0171_all_names_actions

The DM's actions on that page, for the bot (#2294).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

NAME_ACTIONS = (
    f"""
    -- What a DM did on the website's All names page (#2294), waiting for the bot. The website has
    -- no right on the memory tables, so its door only checks the signed-in DM and queues the
    -- action here; the bot applies it through the same MemoryStore paths as Discord's name card
    -- (so the change log, Undo and every list agree), re-checks that the sender is still a DM of
    -- the campaign and that the name is still there, and marks the row. Applied in the order made
    -- (`seq`), so a "spell" then a "merge" never run backwards. `text` is a spelling or another
    -- name (at most 100), `flag` the secret switch or the remove confirmation. `result_id` is the
    -- name whose row the page shows afterwards (the survivor of a merge). `secret` marks or unmarks
    -- one other name (`text`); `hide` is the whole entry's "players don't know yet" (`flag`). A row
    -- lives an hour at most and its words are never logged. Gone with its campaign, left out of
    -- backups, isolated per server, and read back only by the DM who made it.
    CREATE TABLE name_actions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        entity_id   TEXT NOT NULL CHECK (char_length(entity_id) <= 32),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL CHECK (action IN
            ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove')),
        kind        TEXT NOT NULL DEFAULT '' CHECK (char_length(kind) <= 40),
        target_id   TEXT NOT NULL DEFAULT '' CHECK (char_length(target_id) <= 32),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= 100),
        flag        BOOLEAN NOT NULL DEFAULT false,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        result_id   TEXT NOT NULL DEFAULT '' CHECK (char_length(result_id) <= 32),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX name_actions_open ON name_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("name_actions")
    + r"""
    -- Queue one action. The door checks what it can see: the caller is a DM of the campaign, the
    -- name is a confirmed one of it (not a person at the table), the words are within their
    -- limits, a merge target is another confirmed name, a removal carries its confirmation, and
    -- the campaign's queue is not full. The bot checks the rest when it applies it. Answers
    -- 'ok' with the id, or 'bad_action', 'bad_text', 'bad_target', 'needs_confirm', 'not_found'
    -- or 'too_soon'; NULL for anyone who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_name_action_add(
        g BIGINT, c TEXT, ent TEXT, act TEXT, kd TEXT, tgt TEXT, txt TEXT, fl BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        clean TEXT := btrim(regexp_replace(COALESCE(txt, ''), '[[:space:]]+', ' ', 'g'));
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN
               ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove')
        THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF clean ~ '[[:cntrl:]]' OR char_length(clean) > 100
           OR (act IN ('spell', 'add-other', 'remove-other', 'secret') AND clean = '')
           OR (act IN ('kind', 'hide', 'merge', 'remove') AND clean <> '')
           OR (act = 'kind' AND (kd IS NULL OR kd !~ '^[a-z][a-z _-]{0,39}$'))
           OR (act <> 'kind' AND COALESCE(kd, '') <> '')
           OR (act = 'merge' AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))
           OR (act <> 'merge' AND COALESCE(tgt, '') <> '')
           OR (act IN ('secret', 'hide') AND fl IS NULL) THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF act = 'remove' AND fl IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        IF ent IS NULL OR NOT EXISTS (
               SELECT 1 FROM memory_entities e
               WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ent
                 AND e.status = 'confirmed' AND e.type <> 'person') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF act = 'merge' AND (tgt = ent OR NOT EXISTS (
               SELECT 1 FROM memory_entities t
               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt
                 AND t.status = 'confirmed' AND t.type <> 'person')) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('name_action:' || g::TEXT || c, 0));
        DELETE FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 30
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO name_actions (guild_id, campaign_id, id, entity_id, user_id, action, kind,
                                  target_id, text, flag, created_at)
            VALUES (g, c, rid, ent, dmbot_current_user(), act, COALESCE(kd, ''),
                    COALESCE(tgt, ''), clean, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one action, for the DM who made it (nobody else's): 'waiting', 'working',
    -- 'done' or 'failed' with its reason word, and the name to show afterwards. NULL when there is
    -- no such action of the caller's in this campaign.
    CREATE FUNCTION dmbot_web_name_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r name_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
                                  'resultId', r.result_id);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = NAME_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ("name_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The All names page's actions, queued for the bot and read back by the DM who made them.
    (
        "dmbot_web_name_action_add",
        "dmbot_web_name_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)",
    ),
    ("dmbot_web_name_action_read", "dmbot_web_name_action_read(BIGINT, TEXT, TEXT)"),
)
