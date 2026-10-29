"""0168_email_changes

A waiting change of the account email (#2349).
"""

from __future__ import annotations

EMAIL_CHANGES = """
    -- A waiting change of the email DMbot uses (#2349): one row per account (a new ask replaces the
    -- last one), holding the address asked for and only the SHA-256 of the confirm link's token,
    -- like a session cookie. Own row only; the link works for the account that asked.
    CREATE TABLE email_changes (
        user_id     BIGINT PRIMARY KEY REFERENCES web_users ON DELETE CASCADE,
        new_email   TEXT NOT NULL,
        token_hash  TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL
    );
    -- When the person chose their address on the account page: a Discord sign-in keeps it from
    -- then on instead of putting Discord's address back (no existing row changes).
    ALTER TABLE web_users ADD COLUMN email_chosen_at BIGINT;
    ALTER TABLE email_changes ENABLE ROW LEVEL SECURITY;
    ALTER TABLE email_changes FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON email_changes
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());

    -- Whether another account already uses an address (sign-in by email needs one address per
    -- account). The person is the one signed in; the function runs with its owner's rights to
    -- read the other rows, opened by the same setting and policy as dmbot_user_by_email, and
    -- answers only yes or no.
    CREATE FUNCTION dmbot_email_taken(addr TEXT) RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found BOOLEAN;
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.email_lookup', true), '');
    BEGIN
        IF me IS NULL OR addr IS NULL OR addr = '' THEN
            RETURN NULL;
        END IF;
        -- Two accounts confirming the same address at once would both pass the check below: one
        -- lock per address, held until the caller's transaction ends, makes the second one wait
        -- and then see the first one's row.
        PERFORM pg_advisory_xact_lock(hashtextextended(lower(addr), 2349));
        PERFORM set_config('dmbot.email_lookup', 'on', true);
        SELECT EXISTS (
            SELECT 1 FROM web_users
            WHERE user_id <> me
              AND (lower(email) = lower(addr) OR lower(payment_email) = lower(addr))
        ) INTO found;
        PERFORM set_config('dmbot.email_lookup', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_email_taken(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_email_taken(TEXT) FROM PUBLIC;
"""

SQL = EMAIL_CHANGES

USER_ISOLATED_TABLES: tuple[str, ...] = ("email_changes",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Whether another account has an address, for the signed-in person's change of email (#2349).
    ("dmbot_email_taken", "dmbot_email_taken(TEXT)"),
)
