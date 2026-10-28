"""0071_email_records

The email records (#1288).
"""

from __future__ import annotations

EMAIL_RECORDS = """
    -- Deleting an account also deletes the person's sent-email records (#1287; docs/PLAN.md,
    -- "Accounts"). The website's role has no grant on sent_emails (it is everyone's email
    -- history), so this door deletes the signed-in person's own rows and nobody else's.
    -- email_unsubscribed stays on purpose: it is the never-email-me-again list.
    CREATE FUNCTION dmbot_forget_email_records() RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        gone INTEGER;
    BEGIN
        DELETE FROM sent_emails s WHERE s.user_id = dmbot_current_user();
        GET DIAGNOSTICS gone = ROW_COUNT;
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_email_records()'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_email_records() FROM PUBLIC;
    """

SQL = EMAIL_RECORDS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_email_records", "dmbot_forget_email_records()"),  # account deletion, #1287
)
