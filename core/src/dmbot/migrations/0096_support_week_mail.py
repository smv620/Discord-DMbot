"""0096_support_week_mail

The weekly support email's once-only mark (#1514).
"""

from __future__ import annotations

SUPPORT_WEEK_MAIL = """
    -- The week the team's weekly support email was last sent for (#1514): one row, so a restart
    -- never sends it twice. `week` is the day number (Python's date ordinal) of the Monday the
    -- send was for. Counts only; nothing in it is private. A new table, so no existing row changes
    -- and no row-level security needs opening; only the support admin door may read or write it.
    CREATE TABLE support_week_mail (
        id   SMALLINT PRIMARY KEY CHECK (id = 1),
        week BIGINT NOT NULL
    );
    ALTER TABLE support_week_mail ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_week_mail FORCE ROW LEVEL SECURITY;
    CREATE POLICY support_admin_only ON support_week_mail
        USING (dmbot_support_admin() = 'admin')
        WITH CHECK (dmbot_support_admin() = 'admin');
    """

SQL = SUPPORT_WEEK_MAIL

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # The week the weekly support email last went; only the admin door reads or writes it (#1514).
    "support_week_mail",
)
