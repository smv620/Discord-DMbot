"""0067_payment_email."""

from __future__ import annotations

PAYMENT_EMAIL = """
    -- The email Stripe has for the account's payments (#1242; docs/PLAN.md, "Accounts"),
    -- kept from the payment webhook beside the Discord email the website sign-in gives.
    -- Sign-in links and Try It emails use it before the Discord email; move codes go to it
    -- and nowhere else. Never logged or shown back in full.
    ALTER TABLE web_users ADD COLUMN payment_email TEXT
        CHECK (payment_email IS NULL OR char_length(payment_email) BETWEEN 3 AND 254);
    -- When the company said so (its event time): an older event never replaces a newer one.
    ALTER TABLE web_users ADD COLUMN payment_email_at BIGINT;
    -- Sign-in by emailed link finds an account by lower(email) or lower(payment_email); without
    -- this every request would scan web_users.
    CREATE INDEX web_users_by_payment_email ON web_users (lower(payment_email))
        WHERE payment_email IS NOT NULL;
    -- A changed address arrives with the company's customer id, not ours: this door finds the
    -- account by the customer id stored with its plan and sets the address, only for an event
    -- newer than the one already kept. The website's role has no other way to look an account
    -- up by customer id.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY paymail ON web_users FOR ALL TO %I'
            ' USING (dmbot_owner_sync() = ''paymail'')'
            ' WITH CHECK (dmbot_owner_sync() = ''paymail'')',
            current_user);
        EXECUTE format(
            'CREATE POLICY paymail ON entitlements FOR SELECT TO %I'
            ' USING (dmbot_owner_sync() = ''paymail'')',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_set_payment_email(cust TEXT, addr TEXT, happened BIGINT)
        RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        changed INTEGER;
    BEGIN
        IF cust IS NULL OR addr IS NULL OR char_length(addr) NOT BETWEEN 3 AND 254 THEN
            RETURN FALSE;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'paymail', true);
        UPDATE web_users u SET payment_email = addr, payment_email_at = happened
            FROM entitlements e
            WHERE e.user_id = u.user_id AND e.provider_customer_id = cust
              AND (u.payment_email_at IS NULL OR u.payment_email_at < happened);
        GET DIAGNOSTICS changed = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN changed > 0;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_set_payment_email(TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_set_payment_email(TEXT, TEXT, BIGINT) FROM PUBLIC;
    -- Try It's job reads the address through its door: the payment email first.
    CREATE OR REPLACE FUNCTION dmbot_trial_people(since BIGINT, now BIGINT)
        RETURNS TABLE (user_id BIGINT, dmbot_id TEXT, email TEXT, period_end BIGINT,
                       minutes BIGINT, paid BOOLEAN, extensions INTEGER, granted BOOLEAN)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        RETURN QUERY
        SELECT e.user_id, u.dmbot_id, COALESCE(u.payment_email, u.email), e.period_end,
               COALESCE((SELECT sum(h.minutes) FROM owner_hours h
                         WHERE h.owner_user_id = e.user_id), 0)::BIGINT,
               EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = e.user_id
                       AND (p.kind IS NULL OR p.kind = ANY(
                            ARRAY['subscription_started', 'subscription_renewed',
                                  'extra_hours_bought']))),
               (SELECT count(*)::INTEGER FROM trial_extensions t WHERE t.user_id = e.user_id),
               EXISTS (SELECT 1 FROM access_grants g WHERE g.discord_user_id = e.user_id
                       AND g.revoked_at IS NULL
                       AND (g.ends_at IS NULL OR g.ends_at > now))
        FROM entitlements e JOIN web_users u ON u.user_id = e.user_id
        WHERE e.plan = 'try-it' AND e.status = 'active' AND e.period_end >= since;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_trial_people(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """

SQL = PAYMENT_EMAIL

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_set_payment_email", "dmbot_set_payment_email(TEXT, TEXT, BIGINT)"),  # #1242
)
