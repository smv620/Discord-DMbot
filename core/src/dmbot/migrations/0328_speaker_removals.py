"""0328_speaker_removals

A DM removes one speaker's lines from one campaign (#3305, PR 2): the request queue the website
fills, the per-campaign marker that makes the removal hold after a restore, and `removed_at` on
the uploader's confirmations.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SPEAKER_REMOVALS = (
    r"""
    -- "This speaker's lines are gone from this campaign" (#3305). Ids and a time, no words. It is
    -- the same idea as `transcript_erasures` (a person's own "Delete my transcripts", all of a
    -- server) but for ONE campaign, because a DM's removal must never reach another campaign: a
    -- speaker without Discord has a negative number that other campaigns use for someone else, and
    -- a member's lines in another campaign are not the DM's to remove. A restore reads it (every
    -- path: Discord, the website) and leaves the speaker's lines out; it is carried in the
    -- campaign's backup and is never cleared by a restore, so an older copy of the same campaign
    -- cannot bring the lines back. It also keeps a removed speaker's number from being given again.
    CREATE TABLE speaker_erasures (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id <> 0),
        erased_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """
    + _isolate("speaker_erasures")
    + r"""
    -- What was confirmed for a speaker whose lines were then removed stays as the record (who
    -- confirmed, when, which wording), with the time it was removed and no lines left.
    ALTER TABLE import_consents ADD COLUMN removed_at BIGINT;

    -- A DM's request to remove one speaker's lines from one campaign, waiting for the bot. The
    -- website's role cannot touch the transcript tables and the removal must hold each running
    -- session's save lock, so the bot does it (campaigns/speaker_removal.py). Same shape as
    -- `web_line_deletions` (0272): waiting, then removing, then done (with the counts it removed)
    -- or failed (nothing removed). Only ids, a stage and counts: never words. At most one open row
    -- for a speaker in a campaign. Rows go a day after the request. Not in a backup: a restored
    -- campaign starts with none (the marker above is what travels).
    CREATE TABLE speaker_removals (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        requested_by BIGINT NOT NULL CHECK (requested_by > 0),
        speaker      BIGINT NOT NULL CHECK (speaker <> 0),
        state        TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage        TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'removing', 'done', 'failed')),
        error        TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        lines        INT NOT NULL DEFAULT 0,
        sessions     INT NOT NULL DEFAULT 0,
        created_at   BIGINT NOT NULL,
        claimed_at   BIGINT NOT NULL DEFAULT 0,
        updated_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'removing'),
                                  ('done', 'done'), ('failed', 'failed'))),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX speaker_removals_one_open
        ON speaker_removals (guild_id, campaign_id, speaker) WHERE state IN ('waiting', 'working');
    CREATE INDEX speaker_removals_claim ON speaker_removals (guild_id, state, created_at);
    """
    + _isolate("speaker_removals")
    + r"""
    -- dmbot_import_speaker (0327), now never giving a number a removed speaker had (the marker
    -- keeps it): the next number is below every number the campaign has used.
    CREATE OR REPLACE FUNCTION dmbot_import_speaker(g BIGINT, c TEXT, nm TEXT) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        who BIGINT := dmbot_current_user();
        clean TEXT := btrim(regexp_replace(coalesce(nm, ''), '\s+', ' ', 'g'));
        found BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN NULL;
        END IF;
        IF char_length(clean) NOT BETWEEN 1 AND 80 THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('speakers:' || g::TEXT || c, 0));
        SELECT s.user_id INTO found FROM campaign_speakers s
            WHERE s.guild_id = g AND s.campaign_id = c AND lower(s.name) = lower(clean);
        IF found IS NOT NULL THEN
            RETURN found;
        END IF;
        IF (SELECT count(*) FROM campaign_speakers s
            WHERE s.guild_id = g AND s.campaign_id = c) >= 500 THEN
            RETURN NULL;
        END IF;
        INSERT INTO campaign_speakers (guild_id, campaign_id, user_id, name, created_by, created_at)
            VALUES (g, c,
                    LEAST(
                        (SELECT coalesce(min(s.user_id), 0) - 1 FROM campaign_speakers s
                         WHERE s.guild_id = g AND s.campaign_id = c),
                        (SELECT coalesce(min(e.user_id), 0) - 1 FROM speaker_erasures e
                         WHERE e.guild_id = g AND e.campaign_id = c)),
                    clean, who, floor(extract(epoch FROM clock_timestamp()))::BIGINT)
            RETURNING user_id INTO found;
        RETURN found;
    END
    $fn$;

    -- Ask for the removal. DM of the campaign only (NULL otherwise). The speaker must be one of
    -- the campaign's speakers without Discord or have lines in it ('unknown_speaker' otherwise, so
    -- a made-up number queues nothing). One already open for the speaker returns that one (a
    -- second press is safe); a speaker with no lines left is 'nothing'; at most 20 requests an
    -- hour for the campaign ('too_soon'). 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_speaker_remove(g BIGINT, c TEXT, sp BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        who BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        has_lines BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN NULL;
        END IF;
        IF sp IS NULL OR sp = 0 THEN
            RETURN jsonb_build_object('status', 'unknown_speaker');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('speaker_remove:' || g::TEXT || c, 0));
        DELETE FROM speaker_removals d WHERE d.guild_id = g AND d.created_at < now_s - 86400;
        -- A request the bot took and then went quiet on for ten minutes goes back to waiting.
        UPDATE speaker_removals d SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE d.guild_id = g AND d.campaign_id = c AND d.state = 'working'
              AND d.updated_at < now_s - 600;
        SELECT d.id INTO have FROM speaker_removals d
            WHERE d.guild_id = g AND d.campaign_id = c AND d.speaker = sp
              AND d.state IN ('waiting', 'working');
        IF FOUND THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        has_lines := EXISTS (SELECT 1 FROM transcript_lines l
                             JOIN transcript_sessions t
                               ON t.id = l.session_id AND t.guild_id = l.guild_id
                             WHERE l.guild_id = g AND t.campaign_id = c AND l.user_id = sp);
        IF NOT has_lines AND NOT EXISTS (SELECT 1 FROM campaign_speakers s
                                         WHERE s.guild_id = g AND s.campaign_id = c
                                           AND s.user_id = sp) THEN
            RETURN jsonb_build_object('status', CASE WHEN sp > 0 THEN 'nothing'
                                                     ELSE 'unknown_speaker' END);
        END IF;
        IF (SELECT count(*) FROM speaker_removals d WHERE d.guild_id = g AND d.campaign_id = c
            AND d.created_at >= now_s - 3600) >= 20 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO speaker_removals (guild_id, campaign_id, id, requested_by, speaker,
                                      created_at, updated_at)
            VALUES (g, c, rid, who, sp, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where a removal stands: the stage and times for the shared progress shape, the plain error
    -- word, and (once done) how many lines in how many sessions went. DM of the campaign only;
    -- NULL when it is not this campaign's or is more than a day old. Counts only.
    CREATE FUNCTION dmbot_speaker_removal_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT d.state, d.stage, d.error, d.lines, d.sessions, d.created_at, d.updated_at,
               d.speaker INTO r
            FROM speaker_removals d
            WHERE d.guild_id = g AND d.campaign_id = c AND d.id = rid
              AND d.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'lines', r.lines, 'sessions', r.sessions, 'createdAt', r.created_at,
            'updatedAt', r.updated_at, 'speaker', r.speaker::TEXT);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_speaker(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_speaker_remove(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_speaker_removal_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_speaker_remove(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_speaker_removal_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

SQL = SPEAKER_REMOVALS

ISOLATED_TABLES: tuple[str, ...] = ("speaker_erasures", "speaker_removals")

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_speaker_remove", "dmbot_speaker_remove(BIGINT, TEXT, BIGINT)"),
    ("dmbot_speaker_removal_read", "dmbot_speaker_removal_read(BIGINT, TEXT, TEXT)"),
)
