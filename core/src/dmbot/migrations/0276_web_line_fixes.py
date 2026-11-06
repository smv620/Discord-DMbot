"""0276_web_line_fixes

A line fixed from the website (#2858).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

# A line fixed from the website (#2858; the parity row of #2554): what Discord's `/dmbot fix` does,
# queued for the bot. The website's role cannot touch the transcript tables, and writing a line
# means the bot's save lock, the DM screen and the helpers' copies, so the bot does it with the same
# calls as Discord (`SuggestionStore.suggest` and the DM-screen post for a speaker's suggestion;
# `submit_dm`'s checks and `apply_line_fix` for a DM's rewrite). The door repeats every rule that
# can be checked in the database; the bot checks the secret names and consent again when it acts.
WEB_LINE_FIXES = (
    r"""
    -- One row is one fix asked for on the website. `mode` is what the door decided from who asked:
    -- 'suggest' (the line's own speaker, not a DM of the campaign: it waits for the DM) or
    -- 'rewrite' (a DM of the campaign: written at once, as Discord's DM fix is). `words` is the
    -- person's text, 200 characters at most, and the one place a word of a transcript sits here:
    -- the bot blanks it when it finishes (the CHECK below makes that a rule of the table, not a
    -- habit). `basis` is a hash of the line's cleaned words when asked, no words, so the bot can
    -- refuse a line that changed since the person saw it. Rows live a day and go with their
    -- campaign; only the person who asked reads theirs back.
    CREATE TABLE web_line_fixes (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        session_id  TEXT NOT NULL,
        speaker     BIGINT NOT NULL CHECK (speaker > 0),
        started_ms  BIGINT NOT NULL CHECK (started_ms >= 0),
        mode        TEXT NOT NULL CHECK (mode IN ('suggest', 'rewrite')),
        words       TEXT NOT NULL DEFAULT '' CHECK (char_length(words) <= 200),
        basis       TEXT NOT NULL CHECK (basis ~ '^[0-9a-f]{32}$'),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'fixing', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        -- What the person is told on success: 'sent' (a suggestion is with the DM) or 'changed'.
        result      TEXT NOT NULL DEFAULT '' CHECK (result IN ('', 'sent', 'changed')),
        -- True when the change can still be undone from Discord's fix notes (the session runs).
        undo        BOOLEAN NOT NULL DEFAULT FALSE,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'fixing'),
                                  ('done', 'done'), ('failed', 'failed'))),
        CHECK (state IN ('waiting', 'working') OR words = ''),
        CHECK ((state = 'done') = (result <> ''))
    );
    CREATE INDEX web_line_fixes_open ON web_line_fixes (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_line_fixes")
    + r"""
    -- Queue a fix. `who` and `at` name the line (its speaker and when it started), `txt` is the
    -- words already tidied by the caller (control characters out, spaces collapsed). The door
    -- decides the mode from who is asking. Answers 'ok' with the id and the mode (the same id again
    -- for the same words while one is open), and refuses with: 'not_yours' (not a DM of the
    -- campaign and not the line's speaker), 'bad_text' (empty or over 200), 'not_recorded' (the
    -- speaker has not agreed to be recorded now), 'same' (the words are the line's own), 'open'
    -- (suggest: the line already has a fix waiting for the DM), 'many' (suggest: three waiting),
    -- 'no_dm_screen' (suggest: the campaign has no DM screen to put it on) and 'queue_full' (twenty
    -- waiting of that kind in the campaign, a flood guard). NULL for a server the caller is not in,
    -- a session that is not the campaign's, and a line that is not a spoken one.
    CREATE FUNCTION dmbot_web_line_fix_add(
        g BIGINT, c TEXT, sid TEXT, who BIGINT, at BIGINT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        is_dm BOOLEAN;
        how TEXT;
        shown TEXT;
        have TEXT;
        want TEXT := COALESCE(txt, '');
    BEGIN
        IF me IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me);
        -- A DM rewrites (as `submit_dm`); the speaker, if not a DM, suggests (as `submit`).
        IF is_dm THEN
            how := 'rewrite';
        ELSIF me = who THEN
            how := 'suggest';
        ELSE
            RETURN jsonb_build_object('status', 'not_yours');
        END IF;
        IF char_length(want) NOT BETWEEN 1 AND 200 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        SELECT btrim(regexp_replace(coalesce(l.text, l.heard), '\s+', ' ', 'g')) INTO shown
            FROM transcript_lines l
            WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
              AND l.started_ms = at AND l.sidebar IS NULL AND NOT l.typed
            ORDER BY l.id LIMIT 1;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM consent x WHERE x.guild_id = g AND x.user_id = who) THEN
            RETURN jsonb_build_object('status', 'not_recorded');
        END IF;
        IF btrim(regexp_replace(want, '\s+', ' ', 'g')) = shown THEN
            RETURN jsonb_build_object('status', 'same');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('line_fix:' || g::TEXT || c, 0));
        DELETE FROM web_line_fixes f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.created_at < now_s - 86400;
        -- A fix the bot took and then went quiet on for ten minutes goes back to waiting.
        UPDATE web_line_fixes f SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE f.guild_id = g AND f.campaign_id = c AND f.state = 'working'
              AND f.updated_at < now_s - 600;
        SELECT f.id INTO have FROM web_line_fixes f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.user_id = me
              AND f.session_id = sid AND f.speaker = who AND f.started_ms = at
              AND f.mode = how AND f.words = want AND f.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have, 'mode', how);
        END IF;
        IF how = 'suggest' THEN
            IF NOT EXISTS (SELECT 1 FROM campaigns k
                           WHERE k.guild_id = g AND k.id = c
                             AND k.dm_screen_channel_id IS NOT NULL) THEN
                RETURN jsonb_build_object('status', 'no_dm_screen');
            END IF;
            IF EXISTS (SELECT 1 FROM transcript_suggestions s
                       WHERE s.session_id = sid AND s.user_id = who AND s.started_ms = at
                         AND s.state = 'open')
               OR EXISTS (SELECT 1 FROM web_line_fixes f
                          WHERE f.guild_id = g AND f.campaign_id = c AND f.user_id = me
                            AND f.session_id = sid AND f.speaker = who AND f.started_ms = at
                            AND f.mode = 'suggest' AND f.state IN ('waiting', 'working')) THEN
                RETURN jsonb_build_object('status', 'open');
            END IF;
            IF (SELECT count(*) FROM transcript_suggestions s
                WHERE s.campaign_id = c AND s.user_id = me AND s.state = 'open')
               + (SELECT count(*) FROM web_line_fixes f
                  WHERE f.guild_id = g AND f.campaign_id = c AND f.user_id = me
                    AND f.mode = 'suggest' AND f.state IN ('waiting', 'working')) >= 3 THEN
                RETURN jsonb_build_object('status', 'many');
            END IF;
        END IF;
        -- Each mode has its own count: players' suggestions never lock a DM out of a rewrite.
        IF (SELECT count(*) FROM web_line_fixes f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.mode = how
              AND f.state IN ('waiting', 'working')) >= 20
        THEN
            RETURN jsonb_build_object('status', 'queue_full');
        END IF;
        INSERT INTO web_line_fixes (guild_id, campaign_id, id, user_id, session_id, speaker,
                                    started_ms, mode, words, basis, created_at, updated_at)
            VALUES (g, c, rid, me, sid, who, at, how, want, md5(shown), now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid, 'mode', how);
    END
    $fn$;

    -- Where one of the caller's own fixes stands: the state and stage for the shared progress
    -- shape, the plain error word, the mode, what was done (`result`) and whether Discord can
    -- still undo it. Never the words. NULL when it is not the caller's in this campaign, or is
    -- over a day old.
    CREATE FUNCTION dmbot_web_line_fix_read(g BIGINT, c TEXT, fid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_line_fixes%ROWTYPE;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT f.* INTO r FROM web_line_fixes f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.id = fid
              AND f.user_id = dmbot_current_user()
              AND f.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'mode', r.mode, 'result', r.result, 'undo', r.undo, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_line_fix_add(BIGINT, TEXT, TEXT, BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_line_fix_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_line_fix_add(BIGINT, TEXT, TEXT, BIGINT, BIGINT, TEXT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_line_fix_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

SQL = WEB_LINE_FIXES

ISOLATED_TABLES: tuple[str, ...] = ("web_line_fixes",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A line fixed from the transcript page (#2858).
    (
        "dmbot_web_line_fix_add",
        "dmbot_web_line_fix_add(BIGINT, TEXT, TEXT, BIGINT, BIGINT, TEXT)",
    ),
    ("dmbot_web_line_fix_read", "dmbot_web_line_fix_read(BIGINT, TEXT, TEXT)"),
)
