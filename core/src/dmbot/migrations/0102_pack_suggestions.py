"""0102_pack_suggestions

Suggest an entry to a pack (#1537).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

PACK_SUGGESTIONS = (
    f"""
    -- "Suggest to <pack>" (#1537): a following campaign's DM sends one of its own homebrew
    -- entries to the pack's publisher, as #1259 does for house-rules sets. Only the entry's words
    -- cross (kind, name, summary, text, whether players may know it), never who sent it, and
    -- never an entry from a book (the sender's store refuses those). The same two-table shape:
    --   pack_suggestions       in the PUBLISHER's campaign: the entry, the answer, nothing that
    --                          says which campaign, server or DM sent it
    --   pack_suggestions_sent  in the FOLLOWER's campaign: what its DM sent, to which pack, and
    --                          when it was told the answer; linked only by the random id.
    -- The doors across servers are the two functions below, like the house-rules ones; they open
    -- the tables to themselves through a setting they set and put back. The existing
    -- `suggest_door` policy on rule_sets already opens one shared set (house rules or pack) by id.
    CREATE TABLE pack_suggestions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN
            ('spell', 'monster', 'item', 'feat', 'condition', 'rule', 'other')),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        summary     TEXT CHECK (summary IS NULL OR char_length(summary) BETWEEN 1 AND 160),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
        known       BOOLEAN NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('open', 'accepted', 'declined', 'present')),
        created_at  BIGINT NOT NULL,
        resolved_at BIGINT,
        resolved_by BIGINT,
        version     INTEGER,
        noted_at    BIGINT,  -- when the note reached the publisher's DM screen (NULL: not yet)
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (id),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE INDEX pack_suggestions_open ON pack_suggestions (set_id) WHERE status = 'open';
    CREATE TABLE pack_suggestions_sent (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        set_name    TEXT NOT NULL CHECK (char_length(set_name) BETWEEN 1 AND 60),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        sent_by     BIGINT NOT NULL CHECK (sent_by > 0),
        sent_at     BIGINT NOT NULL,
        told_at     BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """
    + _isolate("pack_suggestions")
    + _isolate("pack_suggestions_sent")
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY suggest_door_read ON pack_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggest''
                       AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY suggest_door_add ON pack_suggestions FOR INSERT TO %I
                WITH CHECK (dmbot_owner_sync() = ''suggest''
                            AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY answer_door ON pack_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggestanswer''
                       AND id = ANY(string_to_array(
                           COALESCE(current_setting(''dmbot.suggestion_ids'', true), $q$$q$),
                           $q$,$q$)))',
            current_user);
    END
    $do$;
    -- Adds a suggestion to a pack the caller's campaign follows. 'ok' (with the publisher's
    -- server and campaign, which DMbot uses to post the note and never shows anyone), 'full'
    -- (the pack already has `pack_cap` open suggestions), 'dup' (the pack already has an open one
    -- for that name), or 'gone' (the caller does not follow it, or it is not shared any more).
    CREATE FUNCTION dmbot_suggest_entry(
        sid TEXT, wanted_set TEXT, follower_campaign TEXT, new_kind TEXT, new_name TEXT,
        new_summary TEXT, new_text TEXT, new_known BOOLEAN, now_at BIGINT, pack_cap INTEGER,
        wait_s INTEGER)
        RETURNS TABLE (outcome TEXT, pub_guild BIGINT, pub_campaign TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_set TEXT := COALESCE(current_setting('dmbot.set_id', true), '');
        found_guild BIGINT;
        found_campaign TEXT;
        waiting INTEGER;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM rule_set_follows f
                       WHERE f.guild_id = dmbot_current_guild()
                         AND f.campaign_id = follower_campaign
                         AND f.set_id = wanted_set AND f.kind = 'homebrew' AND f.updating) THEN
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'suggest', true);
        PERFORM set_config('dmbot.set_id', wanted_set, true);
        SELECT s.guild_id, s.campaign_id INTO found_guild, found_campaign
            FROM rule_sets s WHERE s.id = wanted_set AND s.status = 'shared'
                               AND s.kind = 'homebrew';
        IF found_guild IS NULL THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        -- Two tables suggesting at once take turns, so the cap and the name check hold.
        PERFORM pg_advisory_xact_lock(hashtext(wanted_set));
        SELECT count(*) INTO waiting FROM pack_suggestions q
            WHERE q.set_id = wanted_set AND q.status = 'open'
              AND q.created_at > now_at - wait_s;  -- an unanswered one stops counting
        IF waiting >= pack_cap THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'full'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM pack_suggestions q
                   WHERE q.set_id = wanted_set AND q.status = 'open'
                     AND q.created_at > now_at - wait_s
                     AND lower(q.name) = lower(new_name)) THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'dup'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        INSERT INTO pack_suggestions (guild_id, campaign_id, id, set_id, kind, name, summary,
                                      text, known, status, created_at)
            VALUES (found_guild, found_campaign, sid, wanted_set, new_kind, new_name,
                    new_summary, new_text, new_known, 'open', now_at);
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_id', before_set, true);
        RETURN QUERY SELECT 'ok'::TEXT, found_guild, found_campaign;
    END
    $fn$;
    -- The answers to the pack suggestions with these ids (a comma-separated list the caller made
    -- itself): the id is the key, so nothing else of the publisher's can be reached.
    CREATE FUNCTION dmbot_pack_suggestion_answers(ids TEXT)
        RETURNS TABLE (id TEXT, status TEXT, version INTEGER)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_ids TEXT := COALESCE(current_setting('dmbot.suggestion_ids', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'suggestanswer', true);
        PERFORM set_config('dmbot.suggestion_ids', ids, true);
        RETURN QUERY
            SELECT q.id, q.status, q.version FROM pack_suggestions q
            WHERE q.id = ANY(string_to_array(ids, ','));
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.suggestion_ids', before_ids, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_suggest_entry('
            'TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, BIGINT, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_pack_suggestion_answers(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_suggest_entry(
        TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, BIGINT, INTEGER, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_pack_suggestion_answers(TEXT) FROM PUBLIC;
    """
)

SQL = PACK_SUGGESTIONS

ISOLATED_TABLES: tuple[str, ...] = (
    "pack_suggestions",
    "pack_suggestions_sent",
)
