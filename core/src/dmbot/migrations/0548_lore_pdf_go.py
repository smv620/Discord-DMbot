"""0548_lore_pdf_go

The DM's yes to a staged lore PDF queues its read (#4762, step 3).
"""

from __future__ import annotations

# The read reuses the website's lore queue (`lore_uploads`, 0288) and its worker, so the one open
# read a campaign, the server's job cap, the day's allowance and the page's progress poll already
# apply: `source` accepts `pdf` (no text, no link: the file is the campaign's staged one, 0545).
#
# `dmbot_web_lore_pdf_go(g, c, ref, who, chosen, rights)`, for a DM of the campaign only (NULL for
# anyone else): the right-to-use yes is needed (`needs_confirm`); the staged file must be counted
# and still the one the DM saw (`ref` is the short hash the website was given, never the id;
# `not_found` for none or another, `not_counted` before the count); the speaker and the choice are
# 0288's checks; a campaign with a read open is `busy`, and five an hour are `too_soon`. In one
# statement it marks the staged row `reading` and queues the upload, so a second press finds the
# file already being read. It sends nothing to an AI and records nothing else: the bot records the
# confirmation (who, when, the file's SHA-256) before its first AI call, and deletes the staged row
# and file however the read ends. No row is changed except those two.
SQL = r"""
    ALTER TABLE lore_uploads DROP CONSTRAINT lore_uploads_source_check;
    ALTER TABLE lore_uploads ADD CONSTRAINT lore_uploads_source_check
        CHECK (source IN ('paste', 'file', 'link', 'pdf'));

    CREATE FUNCTION dmbot_web_lore_pdf_go(g BIGINT, c TEXT, pdf_ref TEXT, who TEXT, chosen TEXT,
                                          rights BOOLEAN) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        s lore_pdf_staged%ROWTYPE;
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
        IF chosen IS NULL OR chosen NOT IN ('world', 'spoilers', 'smart')
           OR pdf_ref IS NULL OR pdf_ref !~ '^[0-9a-f]{32}$'
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
        SELECT t.* INTO s FROM lore_pdf_staged t WHERE t.guild_id = g AND t.campaign_id = c;
        IF NOT FOUND
           OR substr(encode(sha256(convert_to(s.id, 'UTF8')), 'hex'), 1, 32) <> pdf_ref THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF s.state = 'reading' THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF s.state <> 'estimated' THEN
            RETURN jsonb_build_object('status', 'not_counted');
        END IF;
        DELETE FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at < now_s - 86400;
        UPDATE lore_uploads u SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state = 'working'
              AND u.updated_at < now_s - 600;
        SELECT u.id INTO have FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF (SELECT count(*) FROM lore_uploads u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at >= now_s - 3600) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        UPDATE lore_pdf_staged SET state = 'reading', updated_at = now_s
            WHERE guild_id = g AND campaign_id = c AND id = s.id;
        INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, speaker, choice,
                                  rights_by, rights_at, created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), 'pdf', COALESCE(who, ''), chosen,
                    dmbot_current_user(), now_s, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_lore_pdf_go(BIGINT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_lore_pdf_go(BIGINT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)
        FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_web_lore_pdf_go",
        "dmbot_web_lore_pdf_go(BIGINT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)",
    ),
)
