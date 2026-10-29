"""0063_dmbot_id."""

from __future__ import annotations

# Numbered 0063: 0060 to 0062 (owner gone, shared rulesets, their updates) have merged. A
# migration list may have gaps, so a later number never has to wait for an earlier one.
DMBOT_ID = """
    -- The DMbot ID (#1228; docs/PLAN.md, "Accounts"): a Discord ID in base 36, the Stripe invoice
    -- prefix, fixed for the life of the account. The database makes it (one source of truth for
    -- every account, including the ones made before this), a trigger fills it on insert whatever
    -- the caller says, and another refuses any later change. dmbot.account_id is the same
    -- function in Python, and a test holds the two together.
    CREATE FUNCTION dmbot_base36(n BIGINT) RETURNS TEXT
        LANGUAGE plpgsql IMMUTABLE
        SET search_path = pg_catalog
        AS $fn$
    DECLARE
        digits CONSTANT TEXT := '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
        rest BIGINT := n;
        out TEXT := '';
    BEGIN
        IF n IS NULL OR n <= 0 THEN
            RAISE EXCEPTION 'A Discord ID is a positive number';
        END IF;
        WHILE rest > 0 LOOP
            out := substr(digits, (rest % 36)::INTEGER + 1, 1) || out;
            rest := rest / 36;
        END LOOP;
        IF char_length(out) > 12 THEN
            RAISE EXCEPTION 'That ID is too big for a DMbot ID';
        END IF;
        RETURN out;
    END
    $fn$;
    ALTER TABLE web_users ADD COLUMN dmbot_id TEXT;
    -- Migrations run with no server or person set, so row-level security hides every row:
    -- open the table to this one UPDATE, dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON web_users FOR ALL USING (true) WITH CHECK (true);
    UPDATE web_users SET dmbot_id = dmbot_base36(user_id);
    DROP POLICY migrate_backfill ON web_users;
    ALTER TABLE web_users
        ALTER COLUMN dmbot_id SET NOT NULL,
        ADD CONSTRAINT web_users_dmbot_id_form CHECK (dmbot_id ~ '^[1-9A-Z][0-9A-Z]{0,11}$'),
        ADD CONSTRAINT web_users_dmbot_id_unique UNIQUE (dmbot_id);
    CREATE FUNCTION dmbot_web_users_id() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            NEW.dmbot_id := dmbot_base36(NEW.user_id);
        ELSIF NEW.dmbot_id IS DISTINCT FROM OLD.dmbot_id OR NEW.user_id <> OLD.user_id THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        END IF;
        RETURN NEW;
    END
    $fn$;
    -- Experience points (#1228): all-time minutes of the person set for the transaction. The
    -- website's role has no grant on owner_hours, so, like dmbot_hours_used, this is the one
    -- door: it takes no person, uses the one set for the transaction, and returns one number.
    CREATE FUNCTION dmbot_minutes_total() RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT COALESCE((SELECT sum(minutes) FROM owner_hours
            WHERE owner_user_id = dmbot_current_user()), 0)::BIGINT
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_minutes_total() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_minutes_total() FROM PUBLIC;
    CREATE TRIGGER web_users_dmbot_id BEFORE INSERT OR UPDATE ON web_users
        FOR EACH ROW EXECUTE FUNCTION dmbot_web_users_id();
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_users_id() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """

SQL = DMBOT_ID

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Experience points (#1228): the one door to the person's all-time minutes.
    ("dmbot_minutes_total", "dmbot_minutes_total()"),
)
