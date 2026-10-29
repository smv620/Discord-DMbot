"""0026_feedback."""

from __future__ import annotations

FEEDBACK = """
    -- Messages sent from the website's "Say hello" page (#665). The message is also posted
    -- as a GitHub Discussion (with its date, nothing else); how to reach the sender stays
    -- here, with the team. No Discord id, no IP address: the form needs no sign-in.
    CREATE TABLE feedback (
        id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind          TEXT NOT NULL CHECK (kind IN ('feedback', 'question')),
        message       TEXT NOT NULL CHECK (char_length(message) BETWEEN 1 AND 2000),
        contact       TEXT CHECK (char_length(contact) BETWEEN 1 AND 200),
        discussion    INTEGER NOT NULL,  -- the GitHub Discussion's number
        created_at    BIGINT NOT NULL
    );
    -- Add-only: no policy lets anyone read or change a row (FORCE holds the table's
    -- owner, the bot's role, too; the bot never queries it, and the website's role may
    -- only INSERT and DELETE). The team reads contacts as the database's administrator.
    -- Contacts are kept for a year (privacy page, #665): the website's hourly sweep
    -- deletes older rows with a plain DELETE, which needs no right to read them.
    ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
    ALTER TABLE feedback FORCE ROW LEVEL SECURITY;
    CREATE POLICY add_only ON feedback FOR INSERT WITH CHECK (true);
    CREATE POLICY forget_after_a_year ON feedback FOR DELETE
        USING (dmbot_cleanup() = 'old-feedback' AND created_at <= dmbot_now() - 365 * 86400);
    """

SQL = FEEDBACK
