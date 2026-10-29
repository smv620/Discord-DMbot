"""0072_rule_suggestions

The shared-rules suggestions (#1261).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RULE_SUGGESTIONS = (
    f"""
    -- "Suggest to <set>" (#1259, #1191 part 2b): a following campaign's DM sends one of its own
    -- house rules to the set's publisher. Only the rule's words cross, never who sent it.
    --   rule_set_suggestions       in the PUBLISHER's campaign: the words, the answer, nothing
    --                              that says which campaign, server or DM sent them
    --   rule_set_suggestions_sent  in the FOLLOWER's campaign: what its DM sent, to which set,
    --                              and when it was told the answer. The two are linked only by
    --                              the random id the follower made, which is a key to one row.
    -- The doors across servers are the functions below: one adds a suggestion to a set the
    -- caller follows (and answers where it went, for DMbot to post the note), one tells the
    -- answers to the ids it is given. Like the other doors they open the tables to
    -- themselves through a setting they set and put back.
    CREATE TABLE rule_set_suggestions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        rule        TEXT NOT NULL CHECK (char_length(rule) BETWEEN 1 AND 500),
        instead     TEXT CHECK (instead IS NULL OR char_length(instead) BETWEEN 1 AND 500),
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
    CREATE INDEX rule_set_suggestions_open ON rule_set_suggestions (set_id) WHERE status = 'open';
    CREATE TABLE rule_set_suggestions_sent (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        set_name    TEXT NOT NULL CHECK (char_length(set_name) BETWEEN 1 AND 60),
        rule        TEXT NOT NULL CHECK (char_length(rule) BETWEEN 1 AND 500),
        sent_by     BIGINT NOT NULL CHECK (sent_by > 0),
        sent_at     BIGINT NOT NULL,
        told_at     BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """
    + _isolate("rule_set_suggestions")
    + _isolate("rule_set_suggestions_sent")
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY suggest_door ON rule_sets FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggest'' AND status = ''shared''
                       AND id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY suggest_door_read ON rule_set_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggest''
                       AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY suggest_door_add ON rule_set_suggestions FOR INSERT TO %I
                WITH CHECK (dmbot_owner_sync() = ''suggest''
                            AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY answer_door ON rule_set_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggestanswer''
                       AND id = ANY(string_to_array(
                           COALESCE(current_setting(''dmbot.suggestion_ids'', true), $q$$q$),
                           $q$,$q$)))',
            current_user);
    END
    $do$;
    -- Adds a suggestion to a set the caller's campaign follows. 'ok' (with the publisher's
    -- server and campaign, which DMbot uses to post the note and never shows anyone), 'full'
    -- (the set already has `set_cap` open suggestions), or 'gone' (the caller does not follow it,
    -- or it is not shared any more).
    CREATE FUNCTION dmbot_suggest_rule(
        sid TEXT, wanted_set TEXT, follower_campaign TEXT, new_rule TEXT, new_instead TEXT,
        now_at BIGINT, set_cap INTEGER, wait_s INTEGER)
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
                         AND f.set_id = wanted_set AND f.updating) THEN
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'suggest', true);
        PERFORM set_config('dmbot.set_id', wanted_set, true);
        SELECT s.guild_id, s.campaign_id INTO found_guild, found_campaign
            FROM rule_sets s WHERE s.id = wanted_set AND s.status = 'shared';
        IF found_guild IS NULL THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        -- Two tables suggesting at once take turns, so the cap holds.
        PERFORM pg_advisory_xact_lock(hashtext(wanted_set));
        SELECT count(*) INTO waiting FROM rule_set_suggestions q
            WHERE q.set_id = wanted_set AND q.status = 'open'
              AND q.created_at > now_at - wait_s;  -- an unanswered one stops counting
        IF waiting >= set_cap THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'full'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        INSERT INTO rule_set_suggestions (guild_id, campaign_id, id, set_id, rule, instead,
                                          status, created_at)
            VALUES (found_guild, found_campaign, sid, wanted_set, new_rule, new_instead,
                    'open', now_at);
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_id', before_set, true);
        RETURN QUERY SELECT 'ok'::TEXT, found_guild, found_campaign;
    END
    $fn$;
    -- The answers to the suggestions with these ids (a comma-separated list the caller made
    -- itself): the id is the key, so nothing else of the publisher's can be reached.
    CREATE FUNCTION dmbot_suggestion_answers(ids TEXT)
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
            SELECT q.id, q.status, q.version FROM rule_set_suggestions q
            WHERE q.id = ANY(string_to_array(ids, ','));
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.suggestion_ids', before_ids, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_suggest_rule('
            'TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_suggestion_answers(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_suggest_rule(
        TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, INTEGER, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_suggestion_answers(TEXT) FROM PUBLIC;
    """
)

SQL = RULE_SUGGESTIONS

ISOLATED_TABLES: tuple[str, ...] = (
    "rule_set_suggestions",
    "rule_set_suggestions_sent",
)
