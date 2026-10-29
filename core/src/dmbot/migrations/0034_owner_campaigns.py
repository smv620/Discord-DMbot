"""0034_owner_campaigns."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _setting,
)

OWNER_CAMPAIGNS = (
    _setting("dmbot_owner_sync", "dmbot.owner_sync", "TEXT")
    + """
    -- Which campaigns each person owns, across every server (#437 part 2c; docs/PLAN.md,
    -- "Plans and pricing"). The campaign cap needs "how many campaigns does this person
    -- own", and `campaigns` is isolated per server, so like owner_hours this is a table of
    -- its own: two ids, no server, no name, so reading it reveals nothing about where the
    -- campaigns are. It is scoped to the owner (the meter door reads it with that person
    -- set), and the website's role has no grant on it.
    CREATE TABLE owner_campaigns (
        owner_user_id BIGINT NOT NULL CHECK (owner_user_id > 0),
        campaign_id   TEXT NOT NULL,
        PRIMARY KEY (owner_user_id, campaign_id)
    );
    ALTER TABLE owner_campaigns ENABLE ROW LEVEL SECURITY;
    ALTER TABLE owner_campaigns FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON owner_campaigns FOR SELECT
        USING (owner_user_id = dmbot_current_user());
    -- Written only by the trigger below, which sets dmbot.owner_sync for its own statements
    -- and clears it again. (A guard against code that writes it by mistake, as the meter's
    -- setting is; not against a hostile process.)
    CREATE POLICY sync_write ON owner_campaigns
        USING (dmbot_owner_sync() = 'trigger')
        WITH CHECK (dmbot_owner_sync() = 'trigger');

    -- Keeps the table in step with `campaigns` however a campaign is made, restored, handed
    -- over, deleted or removed with its server. The trigger runs inside a server-scoped
    -- transaction (or the website's, for an accepted hand-over) but writes an owner-scoped
    -- table, and the caller may have no grant on it: so the function runs with its owner's
    -- rights (SECURITY DEFINER), with a search_path fixed below, and does nothing but this
    -- one insert, delete or move.
    CREATE FUNCTION dmbot_sync_owner_campaigns() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trigger', true);
        IF TG_OP = 'INSERT' THEN
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id)
                    VALUES (NEW.owner_user_id, NEW.id) ON CONFLICT DO NOTHING;
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
                INSERT INTO owner_campaigns (owner_user_id, campaign_id)
                    VALUES (NEW.owner_user_id, NEW.id) ON CONFLICT DO NOTHING;
            END IF;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN NULL;
    END
    $fn$;
    -- Fixed: the system catalogue, this schema (the one the migration runs in), and the
    -- temporary schema last, so nothing a caller makes can stand in for a name used above.
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_sync_owner_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_sync_owner_campaigns() FROM PUBLIC;
    CREATE TRIGGER owner_campaigns_sync
        AFTER INSERT OR DELETE OR UPDATE OF owner_user_id ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_sync_owner_campaigns();
    -- A row trigger never sees TRUNCATE, which would leave the table counting campaigns that
    -- are gone: so it is refused, and anything removing campaigns in bulk must use DELETE
    -- (a server's removal does).
    CREATE FUNCTION dmbot_refuse_campaigns_truncate() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        RAISE EXCEPTION 'Use DELETE on campaigns, not TRUNCATE (a trigger keeps owner_campaigns)';
    END
    $fn$;
    CREATE TRIGGER campaigns_no_truncate
        BEFORE TRUNCATE ON campaigns
        FOR EACH STATEMENT EXECUTE FUNCTION dmbot_refuse_campaigns_truncate();

    -- The one count anyone but the owner's own rows may see, for the website's accept of a
    -- hand-over: its role has no grant on owner_campaigns, so it asks this. It returns only
    -- how many campaigns the person set for this transaction owns (dmbot_current_user(), as
    -- the table's own policy), takes no argument, and so cannot be pointed at anyone else.
    CREATE FUNCTION dmbot_owned_campaigns() RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT count(*) FROM owner_campaigns WHERE owner_user_id = dmbot_current_user()
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_owned_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_owned_campaigns() FROM PUBLIC;

    -- The campaigns that exist now. Migrations run with no server set, so row-level security
    -- hides every row: the backfill opens the table it reads and the one it writes for
    -- itself (CLAUDE.md), dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaigns FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON owner_campaigns FOR INSERT WITH CHECK (true);
    INSERT INTO owner_campaigns (owner_user_id, campaign_id)
        SELECT owner_user_id, id FROM campaigns WHERE owner_user_id IS NOT NULL;
    DROP POLICY migrate_backfill ON owner_campaigns;
    DROP POLICY migrate_backfill ON campaigns;
    """
)

SQL = OWNER_CAMPAIGNS

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # Which campaigns an owner has, across servers; kept by a trigger on campaigns (#437).
    "owner_campaigns",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_owned_campaigns", "dmbot_owned_campaigns()"),
)
