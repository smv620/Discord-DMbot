"""0075_support_email

The admin page's door to a ticket's reply address.
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _setting,
)

SUPPORT_EMAIL = (
    _setting("dmbot_support_email_lookup", "dmbot.support_email_lookup", "TEXT")
    + """
    -- The email a support ticket's answer goes to (#1340; Supervisor, 2026-10-15: the team replies
    -- by email to the address on the person's account). The admin page shows it beside
    -- each ticket; it is never copied into the ticket, so it stays the account's own and goes with
    -- the account. Same address the site uses for the account: the payment company's, else the
    -- one Discord gave (web.accounts.account_email).
    -- web_users is forced to a person's own row, so reading another person's needs a door like
    -- dmbot_user_by_email's: a function with its owner's rights that answers only while the admin
    -- door (Database.support_admin) is open, and a read-only policy TO the owner role, open only
    -- while the function has set its own setting. Never the website's role, which could otherwise
    -- set that setting itself and read every account's email.
    CREATE FUNCTION dmbot_support_email(who BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found TEXT;
        before TEXT := COALESCE(current_setting('dmbot.support_email_lookup', true), '');
    BEGIN
        IF dmbot_support_admin() IS DISTINCT FROM 'admin' THEN
            RETURN NULL;
        END IF;
        -- Only for an account that has sent a ticket: the door is one the website's role can open
        -- itself, so it must not be a way to read any account's address. (The ticket check runs
        -- under the admin door the caller already holds; support_tickets is forced to it.)
        IF NOT EXISTS (SELECT 1 FROM support_tickets WHERE user_id = who) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.support_email_lookup', 'on', true);
        SELECT COALESCE(payment_email, email) INTO found FROM web_users WHERE user_id = who;
        PERFORM set_config('dmbot.support_email_lookup', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY support_email_lookup ON web_users FOR SELECT TO %I
                USING (dmbot_support_email_lookup() = ''on'')',
            current_user);
    END
    $do$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_email(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_email(BIGINT) FROM PUBLIC;
    """
)

SQL = SUPPORT_EMAIL

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The admin page reads a ticket's reply address, and only while its door is open (#1340).
    ("dmbot_support_email", "dmbot_support_email(BIGINT)"),
)
