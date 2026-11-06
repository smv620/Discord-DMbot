"""0062_set_updates."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

SET_UPDATES = (
    f"""
    -- Shared rulesets part 2 (#1191): updates to a set, and who may write it.
    --   rule_set_follows.code   the share code a follower used, so its session start can ask
    --                           whether the set has a newer version. A code the publisher
    --                           turned off stops NEW followers only (owner decision): follows
    --                           made before keep getting updates, until Stop sharing.
    --   rule_set_codes.deactivated_at  when a code was turned off, for that comparison (codes
    --                           turned off before this migration have none, and keep their
    --                           old meaning: they do not update earlier follows)
    --   rule_set_follows.declined_version  the version an Undo sent back: DMbot does not
    --                           take it again at a session start, only a newer one; the DM
    --                           can still choose it with Pick changes
    --   rule_set_follows.previous_*, taken_*  the copy before the last update was taken, so
    --                           Undo needs no memory, and who took it (NULL: DMbot did, at a
    --                           session start; "auto")
    --   rule_set_versions.source  who made the version: the publisher's DMs or co-editors,
    --                           an accepted suggestion, or an accepted change found in the
    --                           shared document (the log of who and when is created_by and
    --                           created_at)
    --   rule_set_editors        people the publisher named as co-editors of its set
    --   house_rules.from_set    a rule that came from a followed set in a Cherry pick, as
    --                           "<set> house rules, v4": merged rules rank below the
    --                           campaign's own
    -- New columns with defaults or NULL: no row is rewritten, so no row-level security to open.
    ALTER TABLE rule_set_follows
        ADD COLUMN code TEXT CHECK (code IS NULL OR code ~ '^[A-Z2-7]{{10}}$'),
        ADD COLUMN previous_version INTEGER,
        ADD COLUMN previous_content JSONB,
        ADD COLUMN declined_version INTEGER,
        ADD COLUMN taken_by BIGINT,
        ADD COLUMN taken_source TEXT CHECK (taken_source IS NULL OR taken_source IN ('auto', 'dm'));
    ALTER TABLE rule_set_codes ADD COLUMN deactivated_at BIGINT;
    ALTER TABLE rule_set_versions
        ADD COLUMN source TEXT NOT NULL DEFAULT 'publisher'
        CHECK (source IN ('publisher', 'suggestion', 'document'));
    ALTER TABLE house_rules
        ADD COLUMN from_set TEXT CHECK (from_set IS NULL OR char_length(from_set) <= 200);
    CREATE TABLE rule_set_editors (
        {_memory_scope()}
        set_id      TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        added_by    BIGINT NOT NULL CHECK (added_by > 0),
        added_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, set_id, user_id),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    """
    + _isolate("rule_set_editors")
    + """
    -- The update door: like dmbot_set_by_code (0061) but for a follower asking whether the set it
    -- already follows has a newer version. A code turned off AFTER the follow still answers; one
    -- turned off before it, or a set that is no longer shared, does not. Same pattern as that
    -- door: policies for the function's owner only, keyed on the setting it sets.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY update_door ON rule_set_codes FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate''
                       AND code = current_setting(''dmbot.set_code'', true)
                       AND (active OR deactivated_at >=
                            NULLIF(current_setting(''dmbot.set_followed_at'', true),
                                   '''')::BIGINT))',
            current_user);
        EXECUTE format(
            'CREATE POLICY update_door ON rule_sets FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate'' AND status = ''shared''
                       AND id IN (SELECT set_id FROM rule_set_codes))',
            current_user);
        EXECUTE format(
            'CREATE POLICY update_door ON rule_set_versions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate''
                       AND set_id IN (SELECT id FROM rule_sets))',
            current_user);
        EXECUTE format(
            'CREATE POLICY update_door ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate''
                       AND EXISTS (SELECT 1 FROM rule_sets s
                                   WHERE s.guild_id = campaigns.guild_id
                                     AND s.campaign_id = campaigns.id))',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_set_update_by_code(wanted TEXT, followed_at BIGINT)
        RETURNS TABLE (set_id TEXT, kind TEXT, set_name TEXT, publisher TEXT, version INTEGER,
                       content JSONB)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_code TEXT := COALESCE(current_setting('dmbot.set_code', true), '');
        before_at TEXT := COALESCE(current_setting('dmbot.set_followed_at', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'setupdate', true);
        PERFORM set_config('dmbot.set_code', wanted, true);
        PERFORM set_config('dmbot.set_followed_at', followed_at::TEXT, true);
        RETURN QUERY
            SELECT s.id, s.kind, s.name, c.name, v.version, v.content
            FROM rule_set_codes rc
            JOIN rule_sets s ON s.id = rc.set_id
            JOIN campaigns c ON c.guild_id = s.guild_id AND c.id = s.campaign_id
            JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                AND v.set_id = s.id
            WHERE rc.code = wanted AND s.status = 'shared'
            ORDER BY v.version DESC
            LIMIT 1;
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_code', before_code, true);
        PERFORM set_config('dmbot.set_followed_at', before_at, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_set_update_by_code(TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_set_update_by_code(TEXT, BIGINT) FROM PUBLIC;
    """
)

SQL = SET_UPDATES

ISOLATED_TABLES: tuple[str, ...] = ("rule_set_editors",)
