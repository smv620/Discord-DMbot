"""0066_email_sign_in."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _setting,
)

EMAIL_SIGN_IN = (
    """
    -- Website sign-in by an emailed link (#1231; docs/PLAN.md, "Accounts"). A link is one use and
    -- lasts 15 minutes; only its SHA-256 is kept, like a session cookie, so a database copy
    -- can't sign anyone in. Made under the person's own scope; used (once) by whoever holds the
    -- token, through the same setting as a session cookie's hash.
    CREATE TABLE email_sign_in_links (
        token_hash  TEXT PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES web_users ON DELETE CASCADE,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL,
        used_at     BIGINT
    );
    CREATE INDEX email_sign_in_links_by_user ON email_sign_in_links (user_id);
    ALTER TABLE email_sign_in_links ENABLE ROW LEVEL SECURITY;
    ALTER TABLE email_sign_in_links FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON email_sign_in_links
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());
    -- Redeeming: the one unexpired, unused link whose hash was shown. It can be read and
    -- marked used, never made or changed in any other way.
    -- (A used link stays readable by its holder until it expires: the update's RETURNING is
    -- checked against this policy with the new row, which has `used_at` set.)
    CREATE POLICY by_token_read ON email_sign_in_links FOR SELECT
        USING (token_hash = dmbot_current_session() AND expires_at > dmbot_now());
    CREATE POLICY by_token_use ON email_sign_in_links FOR UPDATE
        USING (token_hash = dmbot_current_session() AND used_at IS NULL
               AND expires_at > dmbot_now())
        WITH CHECK (token_hash = dmbot_current_session());

    -- The name the person last signed in with, kept so a personal link can greet them with no
    -- session open. Nullable: no rows change, so no new policy.
    ALTER TABLE web_users ADD COLUMN display_name TEXT;
    -- The email lookup below compares lower case, so it needs this to avoid reading every row.
    CREATE INDEX web_users_by_email ON web_users (lower(email));

"""
    + _setting("dmbot_email_lookup", "dmbot.email_lookup", "TEXT")
    + """
    -- Who an email address belongs to, for sending a sign-in link. The website's role can't
    -- read web_users without a person set, and nobody is set yet when someone types an
    -- address, so this is the one door: an address in, at most one account number out. An
    -- address shared by two accounts finds neither (a link goes to no one rather than the
    -- wrong person). The API never says whether it found anyone.
    CREATE FUNCTION dmbot_user_by_email(addr TEXT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found BIGINT;
        before TEXT := COALESCE(current_setting('dmbot.email_lookup', true), '');
    BEGIN
        IF addr IS NULL OR addr = '' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.email_lookup', 'on', true);
        -- The email Discord gave, or the one the payment company has (`payment_email`, added by
        -- 0067: a function body is looked up when it runs, so this migration may come first).
        -- Exactly one account must match; an address two accounts share finds neither.
        SELECT min(user_id) INTO found FROM web_users
            WHERE lower(email) = lower(addr) OR lower(payment_email) = lower(addr)
            HAVING count(*) = 1;
        PERFORM set_config('dmbot.email_lookup', before, true);
        RETURN found;
    END
    $fn$;
    -- web_users is forced to a person's own row, and the function runs with its owner's rights,
    -- so it needs a way in: a read-only policy TO the role that owns the function (the one
    -- running this migration), open only while the function has set its own setting. Never the
    -- website's role, which could otherwise set the setting itself and read every account's
    -- email.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY email_lookup ON web_users FOR SELECT TO %I
                USING (dmbot_email_lookup() = ''on'')',
            current_user);
    END
    $do$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_user_by_email(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_user_by_email(TEXT) FROM PUBLIC;
    """
)

SQL = EMAIL_SIGN_IN

USER_ISOLATED_TABLES: tuple[str, ...] = ("email_sign_in_links",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Sign-in by emailed link (#1231).
    ("dmbot_user_by_email", "dmbot_user_by_email(TEXT)"),
)
