"""0386_graph_review

The careful check of the DM's graph clean-up fixes, behind "Share this campaign's story to improve
DMbot" (#3741).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The owner (2026-11-07): when the DM fixes a name no chain linked to the party (#3740), DMbot
# checks whether the story held a clue it missed, and writes up only what it missed. Supervisor's
# ruling on #3741: only for campaigns whose DM turned on "Share this campaign's story to improve
# DMbot", off by default; turning it off deletes every waiting check and every report not yet
# posted.
#
# - `campaigns.share_improve`: the switch. The website queues it like every other setting
#   ('share_improve', value 'on' or 'off'), and the bot applies it (`dm_screen/web_settings.py`).
# - `graph_review_pending`: one row per campaign once the DM has fixed something. Each fix pushes
#   `last_fix_at` on, so the check runs 15 minutes after the last one (the owner's "nothing touched
#   for 15 minutes"). It lives in the database so a restart or a closed browser loses nothing, and
#   `claimed_at` is the bot's lease (milliseconds), so two bot processes never check one batch
#   twice. It holds ids and kinds only, never words. `entity_ids` keeps those entries' scenes from
#   the sweep until the check has read them. After a check the row stays, with no fixes and the
#   outcome (`checked_at`, `checked`, `missed`, `stopped`), so the page can say "Checked: DMbot
#   missed 0" or that a check stopped early (CLAUDE.md: every wait says how it ended).
# - `graph_reviews`: the reports. About DMbot, not the campaign's story, so not in a backup
#   (`campaigns/tables.py`). `speaker_ids` is everyone whose words a report quotes: "Stop recording
#   me" deletes every report quoting that person. `posted_at` is for #3742 (posting to the private
#   repository); nothing posts yet.
#
# Both tables are DM-only: no web role reads them. The status door below gives the DM's clean-up
# page counts and times only. No row changes: no backfill policy.
GRAPH_REVIEW = r"""
    ALTER TABLE campaigns ADD COLUMN share_improve BOOLEAN NOT NULL DEFAULT false;

    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule', 'co_dm',
                               'pause', 'story_mode', 'dndbeyond_invite', 'rulesets',
                               'share_improve'));

    CREATE TABLE graph_review_pending (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        fixes        JSONB NOT NULL DEFAULT '[]'::jsonb
                     CHECK (jsonb_typeof(fixes) = 'array' AND jsonb_array_length(fixes) <= 200),
        entity_ids   TEXT[] NOT NULL DEFAULT '{}',
        first_fix_at BIGINT NOT NULL CHECK (first_fix_at > 0),
        last_fix_at  BIGINT NOT NULL CHECK (last_fix_at >= first_fix_at),
        claimed_at   BIGINT,
        tries        INTEGER NOT NULL DEFAULT 0 CHECK (tries >= 0),
        checked_at   BIGINT,
        checked      INTEGER NOT NULL DEFAULT 0 CHECK (checked >= 0),
        missed       INTEGER NOT NULL DEFAULT 0 CHECK (missed >= 0 AND missed <= checked),
        stopped      BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (guild_id, campaign_id)
    );

    CREATE TABLE graph_reviews (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id             TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        checked        INTEGER NOT NULL CHECK (checked >= 0),
        missed         INTEGER NOT NULL CHECK (missed >= 0 AND missed <= checked),
        report         TEXT NOT NULL CHECK (char_length(report) BETWEEN 1 AND 60000),
        speaker_ids    BIGINT[] NOT NULL DEFAULT '{}',
        review_commit  TEXT CHECK (review_commit ~ '^[0-9a-f]{40}$'),
        prompt_version TEXT NOT NULL CHECK (char_length(prompt_version) BETWEEN 1 AND 40),
        model          TEXT NOT NULL CHECK (char_length(model) BETWEEN 1 AND 80),
        posted_at      BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX graph_reviews_speakers ON graph_reviews USING gin (speaker_ids);

    -- The same door as before (0300), with the new setting: 'on' or 'off'.
    CREATE OR REPLACE FUNCTION dmbot_web_setting_add(
        g BIGINT, c TEXT, st TEXT, val TEXT, fl BOOLEAN
    ) RETURNS JSONB
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
        IF st IS NULL OR st NOT IN ('visibility', 'level', 'retention', 'story_mode',
                                    'dndbeyond_invite', 'rulesets', 'share_improve') THEN
            RETURN jsonb_build_object('status', 'bad_setting');
        END IF;
        IF val IS NULL
           OR (st = 'visibility' AND val NOT IN ('private', 'peek', 'open'))
           OR (st = 'level' AND val NOT IN ('quiet', 'normal'))
           OR (st = 'retention' AND val NOT IN ('forever', '365', '90', '30'))
           OR (st = 'story_mode' AND val NOT IN ('off', 'watch', 'guide'))
           OR (st = 'dndbeyond_invite' AND val <> ''
               AND val !~ '^https://(www[.])?dndbeyond[.]com/campaigns/join/[A-Za-z0-9]{4,64}$'
              )
           OR (st = 'rulesets'
               AND (val !~ '^(2024|2014)/(2024|2014|none)$'
                    OR split_part(val, '/', 1) = split_part(val, '/', 2)))
           OR (st = 'share_improve' AND val NOT IN ('on', 'off')) THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        IF st = 'story_mode' AND val <> 'off' AND fl IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, flag,
                                     created_at)
            VALUES (g, c, rid, dmbot_current_user(), st, val, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;
    -- CREATE OR REPLACE drops the pinned search_path and the grant rule, so both are set again.
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;

    -- The clean-up page's status line (Supervisor, #3741 ruling 2): counts and times only, for a
    -- DM of the campaign; NULL for anyone else. `waiting` while fixes wait (how many, the last
    -- fix's time, and `checkingSince` once the bot is checking them), and `last`: when the last
    -- check ended, how many it checked and missed, and whether it stopped early. Never a word of
    -- a report.
    CREATE FUNCTION dmbot_web_graph_review_status(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'on', COALESCE((SELECT k.share_improve FROM campaigns k
                            WHERE k.guild_id = g AND k.id = c), false),
            'waiting', (SELECT jsonb_build_object(
                            'fixes', jsonb_array_length(p.fixes), 'lastFixAt', p.last_fix_at,
                            'checkingSince', p.claimed_at / 1000, 'tries', p.tries)
                        FROM graph_review_pending p
                        WHERE p.guild_id = g AND p.campaign_id = c
                          AND jsonb_array_length(p.fixes) > 0),
            'last', (SELECT jsonb_build_object(
                         'at', p.checked_at, 'checked', p.checked, 'missed', p.missed,
                         'stopped', p.stopped)
                     FROM graph_review_pending p
                     WHERE p.guild_id = g AND p.campaign_id = c AND p.checked_at IS NOT NULL));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_graph_review_status(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_graph_review_status(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = GRAPH_REVIEW + _isolate("graph_review_pending") + _isolate("graph_reviews")

ISOLATED_TABLES: tuple[str, ...] = ("graph_review_pending", "graph_reviews")

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_graph_review_status", "dmbot_web_graph_review_status(BIGINT, TEXT)"),
)
