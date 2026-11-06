"""0078_support_own

A person's own questions (0077: another PR).
"""

from __future__ import annotations

SUPPORT_OWN = """
    -- A person sees their own questions (#1373): when they sent it, the subject and where it
    -- stands. Never the message, and never anyone else's.
    --   * `read_own` lets a person's session read only rows with their own user_id.
    --   * The website's role has a column right on every column of support_tickets EXCEPT
    --     `message` (WEB_ROLE_GRANTS), so even their own words can't be read back through that
    --     role. The admin page reads the words through dmbot_support_message below, a door that
    --     answers only while the admin door is open (Database.support_admin), exactly like
    --     dmbot_support_email: a Python boundary (tests/test_support.py) whose limits are the
    --     same as before this migration, when the admin page could read the column itself.
    CREATE POLICY read_own ON support_tickets FOR SELECT
        USING (user_id = dmbot_current_user());
    -- The list is one person's tickets, newest first.
    CREATE INDEX support_tickets_by_person ON support_tickets (user_id, created_at DESC);
    CREATE FUNCTION dmbot_support_message(ticket BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        words TEXT;
    BEGIN
        IF dmbot_support_admin() IS DISTINCT FROM 'admin' THEN
            RETURN NULL;
        END IF;
        -- support_tickets is forced to its policies: with the door open, support_admin_read
        -- lets this read the row (the function's owner has the column right).
        SELECT message INTO words FROM support_tickets WHERE id = ticket;
        RETURN words;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_message(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_message(BIGINT) FROM PUBLIC;
    """

SQL = SUPPORT_OWN

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # ... and its words, the same way (#1373): the website's role has no right to the column.
    ("dmbot_support_message", "dmbot_support_message(BIGINT)"),
)
