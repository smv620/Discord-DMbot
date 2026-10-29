"""0086_support_thread

The support flow's replies (#1431).
"""

from __future__ import annotations

SUPPORT_THREAD = """
    -- The support flow (#1431): a ticket becomes a thread. The person's later messages, the AI
    -- helper's answers and the team's replies are rows of `support_replies`; the first message
    -- stays in support_tickets.message. Like that column, a reply's words can be read only through
    -- a door that answers while the admin door (Database.support_admin) is open
    -- (dmbot_support_thread), and the website's role has no column right on them.
    --   who  customer (an emailed reply), ai (DMbot's helper), person (the team)
    -- A reply goes with its ticket (the cascade is not blocked by row-level security), so the
    -- 12-month sweep and account deletion need nothing more.
    ALTER TABLE support_tickets DROP CONSTRAINT support_tickets_status_check;
    ALTER TABLE support_tickets ADD CONSTRAINT support_tickets_status_check
        CHECK (status IN ('new', 'answered', 'closed', 'escalated'));
    -- When the helper may try this ticket again after a failed call, and when its calls began to
    -- fail (a ticket whose calls fail for a day goes to a person).
    ALTER TABLE support_tickets ADD COLUMN ai_next_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE support_tickets ADD COLUMN ai_failed_at BIGINT;
    CREATE TABLE support_replies (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ticket_id  BIGINT NOT NULL REFERENCES support_tickets (id) ON DELETE CASCADE,
        who        TEXT NOT NULL CHECK (who IN ('customer', 'ai', 'person')),
        text       TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 8000),
        created_at BIGINT NOT NULL
    );
    CREATE INDEX support_replies_by_ticket ON support_replies (ticket_id, id);
    ALTER TABLE support_replies ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_replies FORCE ROW LEVEL SECURITY;
    CREATE POLICY support_admin_read ON support_replies FOR SELECT
        USING (dmbot_support_admin() = 'admin');
    CREATE POLICY support_admin_add ON support_replies FOR INSERT
        WITH CHECK (dmbot_support_admin() = 'admin');
    CREATE FUNCTION dmbot_support_thread(ticket BIGINT)
        RETURNS TABLE (id BIGINT, who TEXT, text TEXT, created_at BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        IF dmbot_support_admin() IS DISTINCT FROM 'admin' THEN
            RETURN;
        END IF;
        RETURN QUERY SELECT r.id, r.who, r.text, r.created_at FROM support_replies r
            WHERE r.ticket_id = ticket ORDER BY r.id;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_thread(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_thread(BIGINT) FROM PUBLIC;
    """

SQL = SUPPORT_THREAD

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # The replies of a ticket: only the admin door adds or reads them (#1431).
    "support_replies",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_support_thread", "dmbot_support_thread(BIGINT)"),  # ... and a thread's (#1431)
)
