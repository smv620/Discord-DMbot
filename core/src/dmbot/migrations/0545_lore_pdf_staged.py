"""0545_lore_pdf_staged

A lore PDF sent from the website waits here until the bot has counted its pictures and the DM has
said yes (#4762; Supervisor's ruling on the issue, step 1).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `lore_pdf_staged`: one row for each campaign, ids and numbers only: no file name and no word of
# the file. The file itself sits in the staging bucket under a random key (`p/<token>.pdf`, the
# bucket the map uploads use, whose 1-day lifecycle rule removes what nobody picked up); a new
# file replaces the old row and its object is deleted. A row goes with the read however it ends
# and by the bot's sweep after a day (step 2). The three numbers (`pages`, `pictures`,
# `estimate_usd`) are what the bot's count before the yes writes back; nothing else is kept from
# it. Foreign key `ON UPDATE CASCADE` on the campaign (it moves with its `guild_id`); forced
# row-level security (the table's 100 MB CHECK is the outer bound; the website's cap is lower, see
# `web/lore_pdf.py`); no column names a person (the row is the campaign's, its DMs are checked at
# each door), so "delete my data" has nothing of a person's to remove here (#4148's catalog);
# not in a backup (a file in transit, gone within a day).
#
# Three doors for a DM of the campaign (each NULL for anyone else, 0404's check):
# - `dmbot_web_lore_pdf_stage(g, c, token, sha256, size, now)`: puts the row, replacing one. Gives
#   the replaced object's key, so the web API deletes it;
# - `dmbot_web_lore_pdf_read(g, c)`: the row in words and numbers, `{status: 'none'}` with none;
# - `dmbot_web_lore_pdf_drop(g, c)`: deletes the row (the DM's "Never mind"), gives the key.
SQL = (
    r"""
    CREATE TABLE lore_pdf_staged (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        object_key   TEXT NOT NULL CHECK (object_key ~ '^p/[0-9a-f]{32}[.]pdf$'),
        sha256       TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        size_bytes   BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 104857600),
        state        TEXT NOT NULL DEFAULT 'staged'
            CHECK (state IN ('staged', 'estimating', 'estimated', 'reading', 'done', 'failed')),
        pages        INTEGER CHECK (pages >= 0),
        pictures     INTEGER CHECK (pictures >= 0),
        estimate_usd NUMERIC(10, 4) CHECK (estimate_usd >= 0),
        created_at   BIGINT NOT NULL CHECK (created_at > 0),
        updated_at   BIGINT NOT NULL CHECK (updated_at > 0),
        PRIMARY KEY (guild_id, campaign_id)
    );
    """
    + _isolate("lore_pdf_staged")
    + r"""
    CREATE FUNCTION dmbot_web_lore_pdf_stage(
        g BIGINT, c TEXT, token TEXT, digest TEXT, bytes_in BIGINT, now_s BIGINT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        old TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF token IS NULL OR token !~ '^[0-9a-f]{32}$' THEN
            RETURN jsonb_build_object('status', 'bad_file');
        END IF;
        -- one stage at a time for a campaign: with no old row there is nothing for FOR UPDATE to
        -- lock, so two first files sent together would collide on the key
        PERFORM pg_advisory_xact_lock(hashtextextended('lore_pdf:' || g::TEXT || c, 0));
        SELECT s.object_key INTO old FROM lore_pdf_staged s
            WHERE s.guild_id = g AND s.campaign_id = c;
        DELETE FROM lore_pdf_staged WHERE guild_id = g AND campaign_id = c;
        INSERT INTO lore_pdf_staged (guild_id, campaign_id, id, object_key, sha256,
                                     size_bytes, created_at, updated_at)
            VALUES (g, c, token, 'p/' || token || '.pdf', digest, bytes_in,
                    now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', token, 'replaced', old);
    END
    $fn$;

    CREATE FUNCTION dmbot_web_lore_pdf_read(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        s lore_pdf_staged%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT t.* INTO s FROM lore_pdf_staged t WHERE t.guild_id = g AND t.campaign_id = c;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'none');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok', 'id', s.id, 'state', s.state, 'sizeBytes', s.size_bytes,
            'pages', s.pages, 'pictures', s.pictures, 'estimateUsd', s.estimate_usd,
            'createdAt', s.created_at, 'updatedAt', s.updated_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;

    CREATE FUNCTION dmbot_web_lore_pdf_drop(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        old TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        DELETE FROM lore_pdf_staged WHERE guild_id = g AND campaign_id = c
            RETURNING object_key INTO old;
        RETURN jsonb_build_object('status', 'ok', 'replaced', old);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_lore_pdf_stage(BIGINT, TEXT, TEXT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_lore_pdf_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_lore_pdf_drop(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_lore_pdf_stage(BIGINT, TEXT, TEXT, TEXT, BIGINT, BIGINT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_lore_pdf_read(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_lore_pdf_drop(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("lore_pdf_staged",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_lore_pdf_stage",
        "dmbot_web_lore_pdf_stage(BIGINT, TEXT, TEXT, TEXT, BIGINT, BIGINT)",
    ),
    ("dmbot_web_lore_pdf_read", "dmbot_web_lore_pdf_read(BIGINT, TEXT)"),
    ("dmbot_web_lore_pdf_drop", "dmbot_web_lore_pdf_drop(BIGINT, TEXT)"),
)
