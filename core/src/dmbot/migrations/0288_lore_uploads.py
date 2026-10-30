"""0288_lore_uploads

Upload campaign lore on the website (#3022; part of #2985).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

LORE_UPLOADS = (
    r"""
    -- A DM's lore or backstory given on the website (#3022), waiting for the bot. The website's
    -- role cannot call the AI or touch the memory tables, so its door only checks the signed-in DM
    -- and queues the upload here; the bot reads the row and runs the same read as Discord's "Find
    -- names" (names go to Check new names, connections are queued like Discord's), then BLANKS
    -- `text` and `link`: the text is the DM's own writing and is never kept. What stays is the
    -- right-to-use confirmation (who and when: CLAUDE.md, IP), the choice and the counts. `speaker`
    -- is the answer to "Whose backstory is this?" (an entry id, or none) and `choice` is what the
    -- lore is known as (world general knowledge, spoilers, or a smart guess; #2959). Counts and
    -- stages only, no word of any document. One open upload per campaign; a row is gone with its
    -- campaign, left out of backups and isolated per server.
    CREATE TABLE lore_uploads (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        source      TEXT NOT NULL CHECK (source IN ('paste', 'file', 'link')),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= 4000),
        link        TEXT NOT NULL DEFAULT '' CHECK (char_length(link) <= 2000),
        speaker     TEXT NOT NULL DEFAULT '' CHECK (speaker = '' OR speaker ~ '^[0-9a-f]{32}$'),
        choice      TEXT NOT NULL CHECK (choice IN ('world', 'spoilers', 'smart')),
        rights_by   BIGINT NOT NULL CHECK (rights_by > 0),
        rights_at   BIGINT NOT NULL,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting' CHECK (stage ~ '^[a-z_]{1,40}$'),
        names_found INT NOT NULL DEFAULT 0 CHECK (names_found >= 0),
        links_found INT NOT NULL DEFAULT 0 CHECK (links_found >= 0),
        done        INT NOT NULL DEFAULT 0 CHECK (done >= 0),
        total       INT NOT NULL DEFAULT 0 CHECK (total >= 0),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX lore_uploads_open ON lore_uploads (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX lore_uploads_one_open ON lore_uploads (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("lore_uploads")
    + r"""
    -- Queue one upload for the campaign, for a DM of it in a server on the caller's list (NULL for
    -- anyone else). The right-to-use confirmation is required (`needs_confirm`) and recorded with
    -- who and when. A paste or file carries its text, a link its address (https only), never both;
    -- a speaker, when given, is a confirmed player character or NPC of this campaign. One open
    -- upload a campaign (`busy`: the page reads the open one), five an hour (`too_soon`). A claim
    -- the bot took and went quiet on for ten minutes goes back to waiting; one nobody took in half
    -- an hour ends as `not_taken`. 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_web_lore_add(g BIGINT, c TEXT, src TEXT, txt TEXT, lnk TEXT, who TEXT,
                                       chosen TEXT, rights BOOLEAN) RETURNS JSONB
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
        IF rights IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        IF src IS NULL OR src NOT IN ('paste', 'file', 'link')
           OR chosen IS NULL OR chosen NOT IN ('world', 'spoilers', 'smart')
           OR (src = 'link' AND (COALESCE(txt, '') <> '' OR COALESCE(lnk, '') !~ '^https://[^[:space:]]+$'
                                 OR char_length(lnk) > 2000))
           OR (src <> 'link' AND (btrim(COALESCE(txt, '')) = '' OR char_length(txt) > 4000
                                  OR COALESCE(lnk, '') <> ''))
           OR (COALESCE(who, '') <> '' AND who !~ '^[0-9a-f]{32}$') THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF COALESCE(who, '') <> '' AND NOT EXISTS (
               SELECT 1 FROM memory_entities e
               WHERE e.guild_id = g AND e.campaign_id = c AND e.id = who
                 AND e.status = 'confirmed' AND e.type = 'character' AND e.merged_into IS NULL) THEN
            RETURN jsonb_build_object('status', 'bad_speaker');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('lore_upload:' || g::TEXT || ':' || c, 0));
        DELETE FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at < now_s - 86400;
        UPDATE lore_uploads u SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'working'
              AND u.updated_at < now_s - 600;
        -- (Measured from the last change, not from creation: a taken upload put back to waiting
        -- gets its own half hour.)
        UPDATE lore_uploads u SET state = 'failed', error = 'not_taken', text = '', link = '',
                                  updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'waiting'
              AND u.updated_at < now_s - 1800;
        SELECT u.id INTO have FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF (SELECT count(*) FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at >= now_s - 3600) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, text, link, speaker,
                                  choice, rights_by, rights_at, created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), src, COALESCE(txt, ''), COALESCE(lnk, ''),
                    COALESCE(who, ''), chosen, dmbot_current_user(), now_s, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one upload stands (the id, or the newest of the last day when `uid` is NULL), for a DM
    -- of the campaign: stage, counts and times, never the text, the link or who asked. NULL when
    -- there is none (or for anyone else).
    CREATE FUNCTION dmbot_web_lore_read(g BIGINT, c TEXT, uid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r lore_uploads%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- The same sweeps as the add door, so an upload nobody took ends as failed even when
        -- the page only reads, and the DM is never stuck behind it.
        PERFORM pg_advisory_xact_lock(hashtextextended('lore_upload:' || g::TEXT || ':' || c, 0));
        DELETE FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at < now_s - 86400;
        UPDATE lore_uploads u SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'working'
              AND u.updated_at < now_s - 600;
        -- (Measured from the last change, not from creation: a taken upload put back to waiting
        -- gets its own half hour.)
        UPDATE lore_uploads u SET state = 'failed', error = 'not_taken', text = '', link = '',
                                  updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'waiting'
              AND u.updated_at < now_s - 1800;
        SELECT q.* INTO r FROM lore_uploads q
            WHERE q.guild_id = g AND q.campaign_id = c
              AND ((uid IS NOT NULL AND q.id = uid)
                   OR (uid IS NULL AND q.created_at >= now_s - 86400))
            ORDER BY q.seq DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('id', r.id, 'state', r.state, 'stage', r.stage, 'error', r.error,
            'source', r.source, 'choice', r.choice, 'namesFound', r.names_found,
            'linksFound', r.links_found, 'done', r.done, 'total', r.total,
            'createdAt', r.created_at, 'updatedAt', r.updated_at, 'serverNow', now_s);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_lore_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_lore_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_lore_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_lore_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = LORE_UPLOADS

ISOLATED_TABLES: tuple[str, ...] = ("lore_uploads",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_lore_add",
        "dmbot_web_lore_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)",
    ),
    ("dmbot_web_lore_read", "dmbot_web_lore_read(BIGINT, TEXT, TEXT)"),
)
