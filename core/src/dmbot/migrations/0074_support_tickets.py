"""0074_support_tickets

Private support tickets (#1340).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _setting,
)

SUPPORT_TICKETS = (
    _setting("dmbot_support_admin", "dmbot.support_admin", "TEXT")
    + """
    -- Private support tickets from paying people on the website (#1340; docs/PLAN.md,
    -- "QAbot and support tickets"). They hold personal details, so they never become public
    -- GitHub issues. `feedback` (0026) is the model: the website adds a row and cannot read
    -- it back. The one difference is the admin page, which is served by the website's own
    -- role, so it needs a way to read and close tickets that nobody else has:
    --   dmbot.support_admin  set only by Database.support_admin(): the admin page's API,
    --                        the only code allowed to read a ticket or change its status
    -- The website's role can set that itself (as it can dmbot.grant_writer): which code opens
    -- the door is a Python boundary (tests/test_support.py), and the admin sign-in guards it.
    --   user_id  the signed-in account that sent it (a Discord id, as in web_users)
    --   plan     the plan's id when it was sent, so the team sees who is paying for what
    --   source   where it came from; 'email' is for a later step (reading the mailbox)
    CREATE TABLE support_tickets (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        created_at  BIGINT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        plan        TEXT NOT NULL CHECK (char_length(plan) BETWEEN 1 AND 40),
        subject     TEXT NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 120),
        message     TEXT NOT NULL CHECK (char_length(message) BETWEEN 1 AND 4000),
        source      TEXT NOT NULL DEFAULT 'website' CHECK (source IN ('website', 'email')),
        status      TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'answered', 'closed'))
    );
    CREATE INDEX support_tickets_newest ON support_tickets (created_at DESC);
    ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_tickets FORCE ROW LEVEL SECURITY;
    -- A person can add a ticket only for their own account, and only a new one from the
    -- website: they can't read any ticket (their own included) or change one.
    CREATE POLICY add_own ON support_tickets FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND source = 'website' AND status = 'new');
    -- The admin page's door: read every ticket, and change a status (the column grant lets
    -- nothing else change).
    CREATE POLICY support_admin_read ON support_tickets FOR SELECT
        USING (dmbot_support_admin() = 'admin');
    CREATE POLICY support_admin_status ON support_tickets FOR UPDATE
        USING (dmbot_support_admin() = 'admin') WITH CHECK (dmbot_support_admin() = 'admin');
    -- Deleting an account deletes that person's own tickets (like their grant and sessions);
    -- the hourly sweep deletes tickets older than 12 months without needing to read them.
    CREATE POLICY own_delete ON support_tickets FOR DELETE
        USING (user_id = dmbot_current_user());
    CREATE POLICY forget_after_a_year ON support_tickets FOR DELETE
        USING (dmbot_cleanup() = 'old-tickets' AND created_at <= dmbot_now() - 365 * 86400);
    """
)

SQL = SUPPORT_TICKETS

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # A person adds their own ticket and reads back only their own list, never the words; the
    # support admin door reads everything (#1340, #1373).
    "support_tickets",
)
