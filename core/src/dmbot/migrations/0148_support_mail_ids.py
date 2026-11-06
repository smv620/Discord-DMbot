"""0148_support_mail_ids

Email threading (#2192).
"""

from __future__ import annotations

SUPPORT_MAIL_IDS = """
    -- The Message-ID of every email sent for a support ticket (#2192), so the next one can say
    -- which it answers (In-Reply-To, References) and a mail program shows the whole chain as one
    -- conversation. Random IDs only: no words, no addresses. They go with the ticket (the
    -- cascade), and only the admin door reads or writes them, like the replies.
    CREATE TABLE support_mail_ids (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ticket_id  BIGINT NOT NULL REFERENCES support_tickets (id) ON DELETE CASCADE,
        message_id TEXT NOT NULL
            CHECK (message_id ~ '^<[^<>[:space:]@]{1,100}@[^<>[:space:]@]{1,100}>$'),
        created_at BIGINT NOT NULL
    );
    CREATE INDEX support_mail_ids_by_ticket ON support_mail_ids (ticket_id, id);
    ALTER TABLE support_mail_ids ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_mail_ids FORCE ROW LEVEL SECURITY;
    CREATE POLICY support_admin_only ON support_mail_ids
        USING (dmbot_support_admin() = 'admin')
        WITH CHECK (dmbot_support_admin() = 'admin');
    """

SQL = SUPPORT_MAIL_IDS

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # The IDs of the emails sent for a ticket; only the admin door reads or writes them (#2192).
    "support_mail_ids",
)
