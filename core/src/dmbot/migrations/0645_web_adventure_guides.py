"""0645_web_adventure_guides

Guide me for a Shared story adventure, on the website (#3694, slice F).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Where the table is in the DM's own adventure, asked for on the website and answered by the bot
# with the one function Discord's 🧭 uses (`ui.guide.compose`: `memory.guide.read` and `build`, and
# the clashes), so the two can never disagree. The website's role cannot read the story memory, so
# the bot works the answer out and keeps it on the row for the page to read once. Jobs: `read` (the
# answer), `pick` ("I'm in another chapter": `memory.guide.pick`, then the new answer) and `clear`
# (back to DMbot's guess: `memory.guide.clear`, then the new answer).
#
# `answer` holds names and short summaries the reader took from the DM's book, so it is the book's
# own content: it is read back only by a DM of that one campaign, never logged, and the sweep and
# the door delete a row after a day. Failed or running rows hold nothing of it (the table refuses
# an answer on any row but a finished one). It goes with its campaign and with the person who asked
# (0646), and is left out of backups.
SQL = (
    r"""
    CREATE TABLE web_adventure_guides (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        job         TEXT NOT NULL CHECK (job IN ('read', 'pick', 'clear')),
        document_id TEXT NOT NULL CHECK (document_id ~ '^[0-9a-f]{32}$'),
        chapter_id  TEXT NOT NULL DEFAULT '' CHECK (chapter_id ~ '^([0-9a-f]{32})?$'),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        answer      JSONB NOT NULL DEFAULT '{}'::jsonb
            CHECK (pg_column_size(answer) <= 60000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state = 'failed') = (error <> '')),
        CHECK (state = 'done' OR answer = '{}'::jsonb),
        CHECK ((job = 'pick') = (chapter_id <> ''))
    );
    CREATE INDEX web_adventure_guides_open ON web_adventure_guides (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_adventure_guides_one_open
        ON web_adventure_guides (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_adventure_guides")
    + r"""
    -- Queue a job for one file, for a DM of the campaign in a server on the caller's list (NULL for
    -- anyone else). 'not_guide': the Shared story switch is not on "Guide me". 'gone': no such file
    -- here. 'bad_job': an unknown job, or a pick with no chapter. 'busy': another job is open (its
    -- id comes back). 'too_soon': thirty in an hour. 'ok' with the id otherwise. The bot checks it
    -- all again, and the plan, and whether the chapter is still in the book.
    CREATE FUNCTION dmbot_web_adventure_guide(
        g BIGINT, c TEXT, doc TEXT, kind TEXT, chapter TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        want TEXT := COALESCE(chapter, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF kind IS NULL OR kind NOT IN ('read', 'pick', 'clear')
           OR (kind = 'pick') <> (want <> '')
           OR want !~ '^([0-9a-f]{32})?$' THEN
            RETURN jsonb_build_object('status', 'bad_job');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns k
                       WHERE k.guild_id = g AND k.id = c AND k.story_mode = 'guide') THEN
            RETURN jsonb_build_object('status', 'not_guide');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM adventure_documents a
                       WHERE a.guild_id = g AND a.campaign_id = c AND a.id = doc) THEN
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('adventure_guide:' || g::TEXT || c, 0));
        DELETE FROM web_adventure_guides r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        UPDATE web_adventure_guides r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_adventure_guides r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_adventure_guides r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 30 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_adventure_guides (guild_id, campaign_id, id, user_id, job, document_id,
                                          chapter_id, created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), kind, doc, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- One job's state, and its answer once done, for the DM of the campaign (the book's own words:
    -- NULL for anyone else, 'missing' for a row that is gone).
    CREATE FUNCTION dmbot_web_adventure_guide_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_adventure_guides%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_adventure_guides q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = rid;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('id', r.id, 'job', r.job, 'documentId', r.document_id,
            'state', r.state, 'error', r.error, 'answer', r.answer, 'createdAt', r.created_at,
            'updatedAt', r.updated_at,
            'serverNow', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_guide(BIGINT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_guide_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_guide(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_guide_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("web_adventure_guides",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_adventure_guide", "dmbot_web_adventure_guide(BIGINT, TEXT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_adventure_guide_read", "dmbot_web_adventure_guide_read(BIGINT, TEXT, TEXT)"),
)
