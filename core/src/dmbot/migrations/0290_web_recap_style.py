"""0290_web_recap_style

Recap style on the website (#2985): the examples and standing notes that show how recaps sound.
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

# The limits of Discord's ✍️ Recap style (recaps/style.py: EXAMPLE_MAX_WORDS, MAX_EXAMPLES,
# NOTE_CHARS, MAX_NOTES). A web door cannot import them, so the bot checks the same numbers again
# and a test keeps these in step.
EXAMPLE_WORDS = 600
EXAMPLE_CHARS = 8000
NOTE_CHARS = 300

WEB_RECAP_STYLE = (
    r"""
    -- What a DM chose on the website about how their recaps sound (#2985), waiting for the bot:
    -- add an example recap, remove one, use DMbot's own examples again, add a standing note,
    -- remove one. The website has no right to write the style tables, so its door only checks
    -- the signed-in DM and the shape of the choice and queues it here; the bot applies it with
    -- the same store calls as Discord's panel (the DM check, the three-example and ten-note
    -- limits and the word limit are checked again then). `arg` is the example's or note's words
    -- (`add_example`, `add_note`), an id (`remove_example`, `remove_note`) or nothing
    -- (`use_defaults`); the bot BLANKS the words once applied, as the lore queue does. A row
    -- lives a day at most, is gone with its campaign, left out of backups, isolated per server
    -- and read back only by the DM who made it.
    CREATE TABLE web_recap_style_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL CHECK (action IN ('add_example', 'remove_example',
                                                    'use_defaults', 'add_note', 'remove_note')),
        arg         TEXT NOT NULL DEFAULT '' CHECK (char_length(arg) <= @EXAMPLE_CHARS@),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_recap_style_actions_open ON web_recap_style_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_recap_style_actions")
    + r"""
    -- Queue one choice. The door checks what it can see: the caller is a DM of the campaign, the
    -- action is one Discord offers, and its argument has the right shape (words within the
    -- limits, or an id). Answers 'ok' with the id, or 'bad_action', 'bad_arg' or 'too_soon' (ten
    -- open at once); NULL for anyone who is not a DM of the campaign. A choice the bot took and
    -- went quiet on for ten minutes goes back to waiting; one nobody took in half an hour ends
    -- as failed `not_taken` with its words blanked, so the DM is never stuck behind it.
    CREATE FUNCTION dmbot_web_recap_style_add(g BIGINT, c TEXT, act TEXT, a TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        want TEXT := COALESCE(a, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('add_example', 'remove_example', 'use_defaults',
                                      'add_note', 'remove_note') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF act IN ('add_example', 'add_note') THEN
            want := btrim(want);
        END IF;
        IF (act = 'use_defaults' AND want <> '')
           OR (act IN ('remove_example', 'remove_note') AND want !~ '^[0-9]{1,18}$')
           OR (act = 'add_example' AND (want = '' OR char_length(want) > @EXAMPLE_CHARS@
               OR array_length(regexp_split_to_array(want, '\s+'), 1) > @EXAMPLE_WORDS@))
           OR (act = 'add_note' AND char_length(want) NOT BETWEEN 1 AND @NOTE_CHARS@) THEN
            RETURN jsonb_build_object('status', 'bad_arg');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_style:' || g::TEXT || ':' || c, 0));
        DELETE FROM web_recap_style_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        UPDATE web_recap_style_actions r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        UPDATE web_recap_style_actions r SET state = 'failed', error = 'not_taken', arg = '',
                                             updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'waiting'
              AND r.updated_at < now_s - 1800;
        IF (SELECT count(*) FROM web_recap_style_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_recap_style_actions (guild_id, campaign_id, id, user_id, action, arg,
                                             created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one choice, for the DM who made it (nobody else's): its state, the plain
    -- error word and the times. Never its words. The same stale-choice sweep as the add door, so
    -- a page that only reads still sees an untaken choice end. NULL when there is no such choice
    -- of the caller's in this campaign, or it is over a day old.
    CREATE FUNCTION dmbot_web_recap_style_action(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_recap_style_actions%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_style:' || g::TEXT || ':' || c, 0));
        UPDATE web_recap_style_actions x SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE x.guild_id = g AND x.campaign_id = c AND x.state = 'working'
              AND x.updated_at < now_s - 600;
        UPDATE web_recap_style_actions x SET state = 'failed', error = 'not_taken', arg = '',
                                             updated_at = now_s
            WHERE x.guild_id = g AND x.campaign_id = c AND x.state = 'waiting'
              AND x.updated_at < now_s - 1800;
        SELECT a.* INTO r FROM web_recap_style_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user() AND a.created_at >= now_s - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
            'createdAt', r.created_at, 'updatedAt', r.updated_at, 'serverNow', now_s);
    END
    $fn$;

    -- The campaign's recap style as the DM sees it: the DM's own examples and standing notes
    -- (their words, by position), and whether DMbot's own examples are in use. DM only: the
    -- examples are the DM's own pasted recaps and may name secret things. NULL for anyone who is
    -- not a DM of the campaign.
    CREATE FUNCTION dmbot_web_recap_style(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        ex JSONB;
        nt JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', e.id::TEXT, 'text', e.text)
                                  ORDER BY e.position, e.id), '[]'::jsonb) INTO ex
            FROM recap_style_examples e WHERE e.guild_id = g AND e.campaign_id = c;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', n.id::TEXT, 'text', n.text)
                                  ORDER BY n.position, n.id), '[]'::jsonb) INTO nt
            FROM recap_style_notes n WHERE n.guild_id = g AND n.campaign_id = c;
        RETURN jsonb_build_object('examples', ex, 'notes', nt,
                                  'defaults', jsonb_array_length(ex) = 0);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_style_add(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_style_action(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_style(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_recap_style_add(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_recap_style_action(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_recap_style(BIGINT, TEXT) FROM PUBLIC;
"""
)

SQL = (
    WEB_RECAP_STYLE.replace("@EXAMPLE_CHARS@", str(EXAMPLE_CHARS))
    .replace("@EXAMPLE_WORDS@", str(EXAMPLE_WORDS))
    .replace("@NOTE_CHARS@", str(NOTE_CHARS))
)

ISOLATED_TABLES: tuple[str, ...] = ("web_recap_style_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_recap_style_add", "dmbot_web_recap_style_add(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_recap_style_action", "dmbot_web_recap_style_action(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_recap_style", "dmbot_web_recap_style(BIGINT, TEXT)"),
)
