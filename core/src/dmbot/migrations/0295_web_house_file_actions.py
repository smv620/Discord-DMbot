"""0295_web_house_file_actions

The House-rules file on the website, first slice (#2985): connect, change or stop using the file,
compare now, and upload one once.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The most characters of an uploaded file the bot reads: the house-rules reader refuses a file over
# 200,000 characters unread (docs/PLAN.md, "the house-rules file"). One number for the column check
# and the door; `web/house_file.py` and the page mirror it and tests keep them in step.
TEXT_MAX = 200_000

HOUSE_FILE_ACTIONS = (
    r"""
    -- A DM's request about the campaign's house-rules file, given on the website and waiting for
    -- the bot (#2985): connect or change the linked file (`link`), stop using it (`unlink`),
    -- compare it with DMbot's rules now (`check`) or upload a text file once (`upload`). The
    -- website's role has no grant on `house_rules_file` (a share link can be private), so its
    -- doors only check the signed-in DM and queue the request here; the bot applies it through the
    -- same calls as Discord's 📄 House-rules file buttons and then BLANKS `link` and `text`: the
    -- address and the file's words are never kept here. What stays is the stage, the counts of what
    -- a compare found ("to add, to change, to remove") and the times; never a rule's words. One
    -- open request per campaign. Gone with its campaign, left out of backups, isolated per server.
    CREATE TABLE web_house_file_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL CHECK (action IN ('link', 'unlink', 'check', 'upload')),
        link        TEXT NOT NULL DEFAULT '' CHECK (char_length(link) <= 2000),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= @TEXT_MAX@),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting' CHECK (stage ~ '^[a-z_]{1,40}$'),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        to_add      INT NOT NULL DEFAULT 0 CHECK (to_add >= 0),
        to_change   INT NOT NULL DEFAULT 0 CHECK (to_change >= 0),
        to_remove   INT NOT NULL DEFAULT 0 CHECK (to_remove >= 0),
        rules_read  INT NOT NULL DEFAULT 0 CHECK (rules_read >= 0),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_house_file_actions_open ON web_house_file_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_house_file_actions_one_open
        ON web_house_file_actions (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_house_file_actions")
    + r"""
    -- Queue one request for the campaign, for a DM of it in a server on the caller's list (NULL
    -- for anyone else). `link` and `upload` carry their words (an https address, or the file's
    -- text), `unlink` and `check` carry none. One open request a campaign (`busy`: the page reads
    -- the open one), ten an hour (`too_soon`). A claim the bot took and went quiet on for ten
    -- minutes goes back to waiting; one nobody took in half an hour ends as `not_taken` with its
    -- words blanked. 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_web_house_file_add(g BIGINT, c TEXT, act TEXT, lnk TEXT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('link', 'unlink', 'check', 'upload')
           OR (act = 'link' AND (COALESCE(txt, '') <> ''
                                 OR COALESCE(lnk, '') !~ '^https://[^/?#@[:space:]]+([/?#][^[:space:]]*)?$'
                                 OR char_length(lnk) > 2000))
           OR (act = 'upload' AND (btrim(COALESCE(txt, '')) = '' OR COALESCE(lnk, '') <> ''
                                   OR char_length(txt) > @TEXT_MAX@))
           OR (act IN ('unlink', 'check') AND (COALESCE(lnk, '') <> ''
                                               OR COALESCE(txt, '') <> '')) THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('house_file:' || g::TEXT || ':' || c, 0));
        DELETE FROM web_house_file_actions u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at < now_s - 86400;
        UPDATE web_house_file_actions u SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'working'
              AND u.updated_at < now_s - 600;
        UPDATE web_house_file_actions u SET state = 'failed', error = 'not_taken', link = '',
                                            text = '', updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'waiting'
              AND u.updated_at < now_s - 1800;
        SELECT u.id INTO have FROM web_house_file_actions u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_house_file_actions u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at >= now_s - 3600) >= 10 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_house_file_actions (guild_id, campaign_id, id, user_id, action, link, text,
                                            created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), act, COALESCE(lnk, ''), COALESCE(txt, ''),
                    now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where the campaign's house-rules file stands, for a DM of it: whether a file is connected
    -- and the SITE its link is on (the host only: no login details before an @, no port, no path;
    -- the address is never shown back, as in Discord; the add door refuses an @ in new links but a
    -- link set in Discord may have one, so this strips it too),
    -- and one request's progress (the id, or the newest of the last day when `rid` is NULL): stage,
    -- counts and times, never the link, the text or a rule's words. NULL for anyone not a DM. The
    -- same sweeps as the add door, so a request nobody took ends as failed even when the page
    -- only reads.
    CREATE FUNCTION dmbot_web_house_file_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_house_file_actions%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        linked_to TEXT;
        found_row BOOLEAN;
        req JSONB := NULL;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('house_file:' || g::TEXT || ':' || c, 0));
        DELETE FROM web_house_file_actions u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at < now_s - 86400;
        UPDATE web_house_file_actions u SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'working'
              AND u.updated_at < now_s - 600;
        UPDATE web_house_file_actions u SET state = 'failed', error = 'not_taken', link = '',
                                            text = '', updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'waiting'
              AND u.updated_at < now_s - 1800;
        SELECT substring(f.link FROM '^[a-zA-Z]+://(?:[^/?#@]*@)?([^/?#:@]+)') INTO linked_to
            FROM house_rules_file f WHERE f.guild_id = g AND f.campaign_id = c;
        found_row := FOUND;
        SELECT q.* INTO r FROM web_house_file_actions q
            WHERE q.guild_id = g AND q.campaign_id = c
              AND ((rid IS NOT NULL AND q.id = rid)
                   OR (rid IS NULL AND q.created_at >= now_s - 86400))
            ORDER BY q.seq DESC LIMIT 1;
        IF FOUND THEN
            req := jsonb_build_object('id', r.id, 'action', r.action, 'state', r.state,
                'stage', r.stage, 'error', r.error, 'toAdd', r.to_add, 'toChange', r.to_change,
                'toRemove', r.to_remove, 'rulesRead', r.rules_read, 'createdAt', r.created_at,
                'updatedAt', r.updated_at);
        ELSIF rid IS NOT NULL THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('linked', found_row, 'site', COALESCE(linked_to, ''),
                                  'request', req, 'serverNow', now_s);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_house_file_add(BIGINT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_house_file_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_house_file_add(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_house_file_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = HOUSE_FILE_ACTIONS.replace("@TEXT_MAX@", str(TEXT_MAX))

ISOLATED_TABLES: tuple[str, ...] = ("web_house_file_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_house_file_add", "dmbot_web_house_file_add(BIGINT, TEXT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_house_file_read", "dmbot_web_house_file_read(BIGINT, TEXT, TEXT)"),
)
