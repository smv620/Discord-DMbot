"""0159_npc_notes

An NPC's page: its listed view and private notes (#2265).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

NPC_NOTES = (
    f"""
    -- Every NPC opens its own page, with private notes (#2265; docs/PLAN.md, "Every NPC opens its
    -- own page"). `npc_roster` holds what the NPC list shows the players, per entry, written by
    -- the same pass that keeps the Discord list (`npcs_channel.sync_campaign`, from
    -- `RecallService.roster`), so the page and the list say the same and the website, which has no
    -- right on the memory tables, never needs a second copy of the rules. `view` holds names and
    -- groups only (never a secret name, a clue, a condition, a thread or the DM's note). Gone with
    -- its entry's listing and its campaign; left out of backups (derived, and a backup is
    -- downloadable by anyone in the server).
    CREATE TABLE npc_roster (
        {_memory_scope()}
        entry_id    TEXT NOT NULL,
        view        JSONB NOT NULL,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entry_id)
    );

    -- A person's private note on an NPC: one row for each person and entry, at most 5,000
    -- characters. Only that person ever reads it (the doors below answer the caller's own rows;
    -- not the DM, not another player). Never in a transcript, an AI prompt, the story memory, a log
    -- or a backup. `name` is the entry's name as last listed, so a note whose NPC has gone can
    -- still be shown. `orphaned_at` is NULL while the NPC is listed; when it is merged away the
    -- note moves to the one kept (`MemoryStore.merge`), and when it is rejected or deleted the
    -- note stays 30 days in "Notes on names no longer listed" and then goes (the bot's sweep).
    -- Gone with the campaign.
    CREATE TABLE npc_notes (
        {_memory_scope()}
        entry_id    TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        name        TEXT NOT NULL DEFAULT '' CHECK (char_length(name) <= 200),
        body        TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 5000),
        updated_at  BIGINT NOT NULL,
        orphaned_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, entry_id, user_id)
    );
    CREATE INDEX npc_notes_person ON npc_notes (guild_id, campaign_id, user_id);
    CREATE INDEX npc_notes_orphans ON npc_notes (orphaned_at) WHERE orphaned_at IS NOT NULL;
    """
    + _isolate("npc_roster")
    + _isolate("npc_notes")
    + r"""
    -- One NPC's page for a member of the server (the session's own server list, like the
    -- transcripts): the listed view, the posted recaps that mention the name or another name it is
    -- listed under (a recap the table has already seen), the caller's own note, and, for a DM of
    -- the campaign, the DM's note on the entry. NULL for anyone not in the server; 'not_found'
    -- for an entry that is not listed (a secret, unconfirmed or another campaign's never is).
    CREATE FUNCTION dmbot_web_npc_page(g BIGINT, c TEXT, e TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        shown JSONB;
        is_dm BOOLEAN;
        names TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT r.view INTO shown FROM npc_roster r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.entry_id = e;
        IF shown IS NULL THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        names := ARRAY[lower(shown ->> 'name')] || COALESCE(ARRAY(
            SELECT lower(x) FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(shown -> 'alsoCalled') = 'array'
                     THEN shown -> 'alsoCalled' ELSE '[]'::jsonb END) AS x), ARRAY[]::TEXT[]);
        RETURN jsonb_build_object(
            'status', 'ok',
            'view', shown,
            'isDm', is_dm,
            'recaps', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'sessionAt', j.session_at, 'title', j.title, 'day', j.game_day)
                           ORDER BY j.session_at DESC)
                FROM (
                    SELECT rj.session_at, rj.title, rj.game_day FROM recap_jobs rj
                    WHERE rj.guild_id = g AND rj.campaign_id = c AND rj.status = 'posted'
                      AND EXISTS (SELECT 1 FROM unnest(names) AS n
                                  WHERE char_length(n) >= 3
                                    AND position(n IN lower(rj.summary)) > 0)
                    ORDER BY rj.session_at DESC LIMIT 20) j), '[]'::jsonb),
            'myNote', (SELECT jsonb_build_object('body', n.body, 'updatedAt', n.updated_at)
                       FROM npc_notes n
                       WHERE n.guild_id = g AND n.campaign_id = c AND n.entry_id = e
                         AND n.user_id = dmbot_current_user()),
            'dmNote', CASE WHEN is_dm THEN (
                SELECT NULLIF(m.description, '') FROM memory_entities m
                WHERE m.guild_id = g AND m.campaign_id = c AND m.id = e
                  AND m.status = 'confirmed') END);
    END
    $fn$;

    -- Save or clear the caller's own note on a listed NPC. 'ok', 'bad_text' (over 5,000 characters
    -- or a control character), or 'not_found' (not listed); NULL for anyone not in the server. An
    -- empty note clears it.
    CREATE FUNCTION dmbot_web_npc_note_put(g BIGINT, c TEXT, e TEXT, txt TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        shown JSONB;
        clean TEXT := replace(COALESCE(txt, ''), E'\r\n', E'\n');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT r.view INTO shown FROM npc_roster r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.entry_id = e;
        IF shown IS NULL THEN
            RETURN 'not_found';
        END IF;
        IF clean ~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]' OR char_length(clean) > 5000 THEN
            RETURN 'bad_text';
        END IF;
        IF btrim(clean) = '' THEN
            DELETE FROM npc_notes n
                WHERE n.guild_id = g AND n.campaign_id = c AND n.entry_id = e
                  AND n.user_id = dmbot_current_user();
            RETURN 'ok';
        END IF;
        INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body, updated_at)
            VALUES (g, c, e, dmbot_current_user(), left(shown ->> 'name', 200), clean, now_s)
            ON CONFLICT (guild_id, campaign_id, entry_id, user_id) DO UPDATE
                SET body = EXCLUDED.body, name = EXCLUDED.name, updated_at = EXCLUDED.updated_at,
                    orphaned_at = NULL;
        RETURN 'ok';
    END
    $fn$;

    -- All of the caller's own notes in one campaign, for the download: the NPC's name as last
    -- listed, the note, when it was written, and whether the NPC is still listed. NULL for anyone
    -- not in the server.
    CREATE FUNCTION dmbot_web_npc_notes_mine(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', n.entry_id, 'name', n.name, 'body', n.body,
                       'updatedAt', n.updated_at, 'listed', n.orphaned_at IS NULL)
                       ORDER BY lower(n.name), n.entry_id)
            FROM npc_notes n
            WHERE n.guild_id = g AND n.campaign_id = c AND n.user_id = dmbot_current_user()),
            '[]'::jsonb);
    END
    $fn$;

    -- Delete all of the caller's own notes in one campaign. The number deleted; NULL for anyone
    -- not in the server.
    CREATE FUNCTION dmbot_web_npc_notes_delete(g BIGINT, c TEXT) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        gone BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        DELETE FROM npc_notes n
            WHERE n.guild_id = g AND n.campaign_id = c AND n.user_id = dmbot_current_user();
        GET DIAGNOSTICS gone = ROW_COUNT;
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_page(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_note_put(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_notes_mine(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_notes_delete(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_npc_page(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_npc_note_put(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_npc_notes_mine(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_npc_notes_delete(BIGINT, TEXT) FROM PUBLIC;
    """
)

SQL = NPC_NOTES

ISOLATED_TABLES: tuple[str, ...] = (
    "npc_roster",
    "npc_notes",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # An NPC's page and a person's own private notes on it, for any member of the server (#2265).
    ("dmbot_web_npc_page", "dmbot_web_npc_page(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_npc_note_put", "dmbot_web_npc_note_put(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_npc_notes_mine", "dmbot_web_npc_notes_mine(BIGINT, TEXT)"),
    ("dmbot_web_npc_notes_delete", "dmbot_web_npc_notes_delete(BIGINT, TEXT)"),
)
