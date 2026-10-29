"""0061_rule_sets."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RULE_SETS = (
    f"""
    -- Shared rulesets (#1190; docs/PLAN.md, "Shared rulesets"): the one place rules cross from
    -- one campaign to another. A DM of a paid campaign publishes a set of its own house rules;
    -- another campaign's DM may follow it, in the same server or from another with a share
    -- code. Only rules cross: a set's content is house-rule text and the document link,
    -- never story, names, transcripts, notes or anything from a shared rulebook or adventure.
    --   rule_sets          the published set, in its publisher's campaign (server-isolated)
    --   rule_set_versions  each version's content; version 1 is the rules when it was shared
    --   rule_set_codes     share codes the publishing DM made (a code is the capability to see
    --                      the set; unguessable; turned off by the DM)
    --   rule_set_follows   a follower campaign's own copy of the version it uses, so its
    --                      rules keep working if the set goes (and for its backup)
    -- Every table is isolated to its own server and campaign like the rest. The one door
    -- across servers is `dmbot_set_by_code`: it reads a shared set by an active code and
    -- returns that set's name, its publisher campaign's name and its latest version, nothing
    -- else (like the pause and orphan doors, it opens the tables to itself through a setting
    -- it sets and puts back).
    CREATE TABLE rule_sets (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        kind       TEXT NOT NULL CHECK (kind IN ('house_rules', 'homebrew')),
        name       TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        status     TEXT NOT NULL CHECK (status IN ('shared', 'unshared')),
        created_by BIGINT NOT NULL CHECK (created_by > 0),
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (id)
    );
    CREATE TABLE rule_set_versions (
        {_memory_scope()}
        set_id     TEXT NOT NULL,
        version    INTEGER NOT NULL CHECK (version >= 1),
        content    JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
        created_by BIGINT NOT NULL CHECK (created_by > 0),
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, set_id, version),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE rule_set_codes (
        {_memory_scope()}
        code       TEXT NOT NULL CHECK (code ~ '^[A-Z2-7]{{10}}$'),
        set_id     TEXT NOT NULL,
        active     BOOLEAN NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, code),
        UNIQUE (code),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE rule_set_follows (
        {_memory_scope()}
        set_id       TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('house_rules', 'homebrew')),
        set_name     TEXT NOT NULL CHECK (char_length(set_name) BETWEEN 1 AND 60),
        publisher    TEXT NOT NULL CHECK (char_length(publisher) BETWEEN 1 AND 100),
        version      INTEGER NOT NULL CHECK (version >= 1),
        content      JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
        position     INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
        updating     BOOLEAN NOT NULL,
        followed_by  BIGINT NOT NULL CHECK (followed_by > 0),
        followed_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, set_id)
    );
    -- A campaign shares one house-rules set, and follows one.
    CREATE UNIQUE INDEX rule_sets_one_house_set
        ON rule_sets (guild_id, campaign_id) WHERE kind = 'house_rules';
    CREATE UNIQUE INDEX rule_set_follows_one_house_set
        ON rule_set_follows (guild_id, campaign_id) WHERE kind = 'house_rules';
    """
    + "".join(
        _isolate(t)
        for t in ("rule_sets", "rule_set_versions", "rule_set_codes", "rule_set_follows")
    )
    + """
    -- The door. Note: the policies below also let a caller read their OWN server's rows (the
    -- plain guild_isolation policy already does); what they add is the one set a code opens.
    -- The `campaigns` policy has no code condition of its own on purpose: its sub-select on
    -- rule_sets is itself filtered by these row-level rules, so it reaches only a set whose
    -- active code equals the one the function was given. Both settings are transaction-local,
    -- so an error inside the function rolls them back; the restore lines are for success.
    -- TO the role that owns the function (the one running this migration), never the
    -- website's role. Each policy needs the setting the function sets, and the code it sets.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY code_door ON rule_set_codes FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode''
                       AND code = current_setting(''dmbot.set_code'', true) AND active)',
            current_user);
        EXECUTE format(
            'CREATE POLICY code_door ON rule_sets FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode'' AND status = ''shared''
                       AND id IN (SELECT set_id FROM rule_set_codes))',
            current_user);
        EXECUTE format(
            'CREATE POLICY code_door ON rule_set_versions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode''
                       AND set_id IN (SELECT id FROM rule_sets))',
            current_user);
        EXECUTE format(
            'CREATE POLICY code_door ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode''
                       AND EXISTS (SELECT 1 FROM rule_sets s
                                   WHERE s.guild_id = campaigns.guild_id
                                     AND s.campaign_id = campaigns.id))',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_set_by_code(wanted TEXT)
        RETURNS TABLE (set_id TEXT, kind TEXT, set_name TEXT, publisher TEXT, version INTEGER,
                       content JSONB)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_code TEXT := COALESCE(current_setting('dmbot.set_code', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'setcode', true);
        PERFORM set_config('dmbot.set_code', wanted, true);
        RETURN QUERY
            SELECT s.id, s.kind, s.name, c.name, v.version, v.content
            FROM rule_set_codes rc
            JOIN rule_sets s ON s.id = rc.set_id
            JOIN campaigns c ON c.guild_id = s.guild_id AND c.id = s.campaign_id
            JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                AND v.set_id = s.id
            WHERE rc.code = wanted AND rc.active AND s.status = 'shared'
            ORDER BY v.version DESC
            LIMIT 1;
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_code', before_code, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_set_by_code(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_set_by_code(TEXT) FROM PUBLIC;
"""
)

SQL = RULE_SETS

ISOLATED_TABLES: tuple[str, ...] = (
    "rule_sets",
    "rule_set_versions",
    "rule_set_codes",
    "rule_set_follows",
)
