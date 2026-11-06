"""0037_pause."""

from __future__ import annotations

PAUSE = """
    -- Pausing a campaign (#957; docs/PLAN.md, "Plans and pricing"): a paused campaign keeps all
    -- its data, can't start a session, and doesn't count toward the owner's campaign cap. The
    -- owner pauses and unpauses it; a downgrade or a lapse pauses the ones over the new cap.
    ALTER TABLE campaigns ADD COLUMN paused BOOLEAN NOT NULL DEFAULT FALSE;
    -- The owner-level copy the cap counts from (0034) follows it. A new column with a default:
    -- no rows are rewritten, so no row-level security to open.
    ALTER TABLE owner_campaigns ADD COLUMN paused BOOLEAN NOT NULL DEFAULT FALSE;

    -- 0034's trigger function, now carrying `paused`. (CREATE OR REPLACE resets the function's
    -- settings, so the fixed search_path is set again below.)
    CREATE OR REPLACE FUNCTION dmbot_sync_owner_campaigns() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trigger', true);
        IF TG_OP = 'INSERT' THEN
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id, paused)
                    VALUES (NEW.owner_user_id, NEW.id, NEW.paused) ON CONFLICT DO NOTHING;
            END IF;
        ELSIF TG_OP = 'DELETE' THEN
            IF OLD.owner_user_id IS NOT NULL THEN
                DELETE FROM owner_campaigns
                    WHERE owner_user_id = OLD.owner_user_id AND campaign_id = OLD.id;
            END IF;
        ELSIF OLD.owner_user_id IS DISTINCT FROM NEW.owner_user_id THEN
            IF OLD.owner_user_id IS NOT NULL THEN
                DELETE FROM owner_campaigns
                    WHERE owner_user_id = OLD.owner_user_id AND campaign_id = OLD.id;
            END IF;
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id, paused)
                    VALUES (NEW.owner_user_id, NEW.id, NEW.paused) ON CONFLICT DO NOTHING;
            END IF;
        ELSIF OLD.paused IS DISTINCT FROM NEW.paused AND NEW.owner_user_id IS NOT NULL THEN
            UPDATE owner_campaigns SET paused = NEW.paused
                WHERE owner_user_id = NEW.owner_user_id AND campaign_id = NEW.id;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN NULL;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_sync_owner_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    DROP TRIGGER owner_campaigns_sync ON campaigns;
    CREATE TRIGGER owner_campaigns_sync
        AFTER INSERT OR DELETE OR UPDATE OF owner_user_id, paused ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_sync_owner_campaigns();

    -- The count the cap uses (0034) leaves paused campaigns out. Its grants stay as they were.
    CREATE OR REPLACE FUNCTION dmbot_owned_campaigns() RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT count(*) FROM owner_campaigns
            WHERE owner_user_id = dmbot_current_user() AND NOT paused
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_owned_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;

    -- A downgrade or a lapse leaves an owner with more campaigns than the plan covers: this
    -- pauses the ones over `keep`, the most recently played staying active, and returns what it
    -- paused (so the owner can be told once). The campaigns are in other servers, which an
    -- owner-level call can't reach, so (like 0034's trigger) it runs with its owner's rights and
    -- opens `campaigns` to itself for this one person only, through a setting it sets and puts
    -- back. A guard against code that does this by mistake, not against a hostile process. Only
    -- the bot's role may call it (not the website's).
    -- Opens `campaigns` to the pause function only: TO the role that owns the function
    -- (the one running this migration), never the website's role, which could otherwise
    -- set the setting itself and read every campaign a person owns in every server.
    -- SELECT and UPDATE only: it can't insert or delete.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY pause_read ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''pause'' AND owner_user_id = dmbot_current_user())',
            current_user);
        EXECUTE format(
            'CREATE POLICY pause_by_owner ON campaigns FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''pause'' AND owner_user_id = dmbot_current_user())
                WITH CHECK (dmbot_owner_sync() = ''pause''
                            AND owner_user_id = dmbot_current_user())',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_pause_over_cap(keep INTEGER, live TEXT[] DEFAULT '{}')
        RETURNS TABLE (campaign_id TEXT, server_id BIGINT, campaign_name TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF me IS NULL OR keep IS NULL OR keep < 0 THEN
            RETURN;
        END IF;
        -- The common case, nothing over the cap: answer from the small owner table before
        -- locking or scanning anything (this runs on every start).
        IF (SELECT count(*) FROM owner_campaigns oc
            WHERE oc.owner_user_id = me AND NOT oc.paused) <= keep THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'pause', true);
        -- Same lock order as every other caller (campaign row first, then the per-person
        -- lock of campaign_cap), rows in a fixed order so two of these can't cross: a
        -- hand-over holding one campaign while it waits for the person lock must not meet
        -- us holding the person lock while we wait for that campaign.
        PERFORM 1 FROM campaigns c WHERE c.owner_user_id = me
            ORDER BY c.guild_id, c.id FOR UPDATE;
        PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.owner_slots:' || me::text, 0));
        RETURN QUERY
        WITH ranked AS (
            SELECT c.id AS cid, c.guild_id AS gid,
                   -- A campaign DMbot is listening to now is kept first: pausing a live
                   -- table is refused everywhere else ("stop it first").
                   row_number() OVER (
                       ORDER BY (c.id = ANY(live)) DESC,
                                COALESCE(c.last_played_at, c.created_at) DESC,
                                c.created_at DESC, c.id) AS n
            FROM campaigns c
            WHERE c.owner_user_id = me AND NOT c.paused
        ), gone AS (
            UPDATE campaigns c SET paused = TRUE
            FROM ranked r
            WHERE c.id = r.cid AND c.guild_id = r.gid AND r.n > keep
            RETURNING c.id, c.guild_id, c.name
        )
        SELECT gone.id, gone.guild_id, gone.name FROM gone ORDER BY gone.name;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[]) FROM PUBLIC;
"""

SQL = PAUSE
