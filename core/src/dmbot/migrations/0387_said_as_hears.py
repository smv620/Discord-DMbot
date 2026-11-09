"""0387_said_as_hears

"Hear it" on the website's Names page (#3807): a name played in the recap narrator's voice.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The website's web container is never given the voice company's key (#2419), so a press on 🔊 Hear
# it is a row the bot answers: the bot makes the clip with the narrator's voice and puts it on the
# row, the page fetches it once (the door clears the clip as it hands it over) and rows go within
# an hour. The row holds the words the DM typed (at most 80 characters, which may be unsaved) and
# the clip: both are private to the asker, never logged, never in a backup, never in a transcript.
# The door checks the caller is a DM of the campaign, holds the campaign to 20 presses a minute
# and 10 waiting at once; the bot checks again that the sender still is, and the plan.
SQL = (
    r"""
    CREATE TABLE said_as_hears (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 80),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        audio       BYTEA CHECK (audio IS NULL OR octet_length(audio) <= 2000000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        tries       INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX said_as_hears_claim ON said_as_hears (guild_id, state, created_at);
    """
    + _isolate("said_as_hears")
    + r"""
    -- Queue one press. NULL unless the caller is a DM of the campaign (in a server on the
    -- session's list). Answers 'ok' with the id, or 'too_soon' (20 presses in the last minute for
    -- this campaign, or 10 still waiting).
    CREATE FUNCTION dmbot_web_hear_add(g BIGINT, c TEXT, txt TEXT) RETURNS JSONB
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
        IF txt IS NULL OR char_length(txt) NOT BETWEEN 1 AND 80 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('said_as_hear:' || g::TEXT || c, 0));
        DELETE FROM said_as_hears h
            WHERE h.guild_id = g AND h.campaign_id = c AND h.created_at < now_s - 3600;
        IF (SELECT count(*) FROM said_as_hears h
            WHERE h.guild_id = g AND h.campaign_id = c AND h.created_at >= now_s - 60) >= 20
           OR (SELECT count(*) FROM said_as_hears h
               WHERE h.guild_id = g AND h.campaign_id = c
                 AND h.state IN ('waiting', 'working')) >= 10 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO said_as_hears (guild_id, campaign_id, id, user_id, text, created_at)
            VALUES (g, c, rid, dmbot_current_user(), txt, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one press stands, for the DM who made it (nobody else's); NULL when there is no such
    -- press of the caller's. A finished press hands over its clip once, as base64, and the clip is
    -- cleared from the row as it goes (a later read says 'done' with no audio).
    CREATE FUNCTION dmbot_web_hear_read(g BIGINT, c TEXT, hid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r said_as_hears%ROWTYPE;
        clip BYTEA;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT h.* INTO r FROM said_as_hears h
            WHERE h.guild_id = g AND h.campaign_id = c AND h.id = hid
              AND h.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        clip := r.audio;
        IF clip IS NOT NULL THEN
            UPDATE said_as_hears h SET audio = NULL
                WHERE h.guild_id = g AND h.id = hid;
        END IF;
        RETURN jsonb_build_object(
            'state', r.state, 'error', r.error, 'createdAt', r.created_at,
            'claimedAt', r.claimed_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT,
            'audio', CASE WHEN clip IS NULL THEN NULL ELSE encode(clip, 'base64') END);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_hear_add(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_hear_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_hear_add(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_hear_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("said_as_hears",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_hear_add", "dmbot_web_hear_add(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_hear_read", "dmbot_web_hear_read(BIGINT, TEXT, TEXT)"),
)
