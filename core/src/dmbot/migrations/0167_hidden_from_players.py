"""0167_hidden_from_players

A name the players don't know yet (#2321).
"""

from __future__ import annotations

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
HIDDEN_FROM_PLAYERS = """
    -- A name the players don't know yet (#2321): the DM's switch on any entry ("Players don't
    -- know this one yet"), apart from a clue's audience (`audience_limited`, who knows a clue),
    -- which keeps its own meaning. Off for every existing entry, so no row changes; the column
    -- has a default and is added, not updated, so no row-level security needs opening.
    ALTER TABLE memory_entities ADD COLUMN hidden_from_players BOOLEAN NOT NULL DEFAULT false;

    -- A player's character is named to everyone (the transcript file's labels, the table list),
    -- so it can never be hidden: whichever path makes an entry a player's character (an import,
    -- a change of kind, the "plays" answer, a merge) clears the flag in the same write. One
    -- trigger covers them all, the SQL functions that set `played_by` included. It only edits
    -- the row being written, so it needs no extra rights.
    CREATE FUNCTION dmbot_unhide_player_characters() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF NEW.hidden_from_players AND (
            NEW.played_by IS NOT NULL OR NEW.party OR NEW.role = 'player_character'
        ) THEN
            NEW.hidden_from_players := false;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER memory_entities_unhide_players
        BEFORE INSERT OR UPDATE ON memory_entities
        FOR EACH ROW EXECUTE FUNCTION dmbot_unhide_player_characters();

    -- The two recap functions that list the names a recap's text is checked against, and the
    -- names of a shared recap page, now count such an entry as secret. CREATE OR REPLACE resets
    -- a function's own settings, so each one's search_path is set again below.
    CREATE OR REPLACE FUNCTION dmbot_recap_check_names(g BIGINT, c TEXT, terms INT, outside TEXT)
    RETURNS JSONB
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
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb),
            'recorded', COALESCE((
                SELECT jsonb_agg(c2.user_id) FROM consent c2
                WHERE c2.guild_id = g AND c2.terms_version = terms
                  AND (outside IS NULL OR c2.outside_to = outside)), '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

SQL = HIDDEN_FROM_PLAYERS
