"""0030_access_grants."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _setting,
)

ACCESS_GRANTS = (
    _setting("dmbot_grant_writer", "dmbot.grant_writer", "TEXT")
    + """
    -- Free access given by hand (#771; docs/PLAN.md, "Free access and the admin page").
    -- One row per Discord account: no link to web_users, since a grant must work for
    -- someone who never signed in to the website. "guild": Guild's caps from plans.json;
    -- "unlimited": no caps. A grant ends at ends_at or when revoked (kept, with
    -- revoked_at, for the log). entitlements is never written for this.
    --   dmbot.grant_writer  set only by Database.grant_writer(): the admin page's API,
    --                       the only code allowed to give, change or revoke a grant
    CREATE TABLE access_grants (
        discord_user_id BIGINT PRIMARY KEY CHECK (discord_user_id > 0),
        level           TEXT NOT NULL CHECK (level IN ('guild', 'unlimited')),
        ends_at         BIGINT,
        note            TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 200),
        granted_by      TEXT NOT NULL CHECK (char_length(granted_by) BETWEEN 3 AND 320),
        granted_at      BIGINT NOT NULL,
        revoked_at      BIGINT
    );
    -- A person reads (and, deleting their account, deletes) only their own grant; the
    -- grant writer reads and writes any. A person's own row holds granted_by (the admin's
    -- email) and the note too: code reading it for them (entitlements) selects only the
    -- level and dates, and neither may ever reach /me. The website's role can set
    -- dmbot.grant_writer itself (as it can dmbot.plan_writer): which code opens that door
    -- is a Python boundary (tests/test_access.py), and part 2's admin sign-in guards it.
    ALTER TABLE access_grants ENABLE ROW LEVEL SECURITY;
    ALTER TABLE access_grants FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON access_grants FOR SELECT
        USING (discord_user_id = dmbot_current_user());
    CREATE POLICY own_delete ON access_grants FOR DELETE
        USING (discord_user_id = dmbot_current_user());
    CREATE POLICY grant_writer ON access_grants FOR ALL
        USING (dmbot_grant_writer() = 'admin') WITH CHECK (dmbot_grant_writer() = 'admin');

    -- Who gave, changed or revoked a grant, and when (add-only; ids, and the admin's
    -- own email only here).
    CREATE TABLE access_log (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        admin_email     TEXT NOT NULL CHECK (char_length(admin_email) BETWEEN 3 AND 320),
        action          TEXT NOT NULL CHECK (action IN ('grant', 'change', 'revoke')),
        discord_user_id BIGINT NOT NULL CHECK (discord_user_id > 0),
        at              BIGINT NOT NULL
    );
    ALTER TABLE access_log ENABLE ROW LEVEL SECURITY;
    ALTER TABLE access_log FORCE ROW LEVEL SECURITY;
    CREATE POLICY grant_writer_add ON access_log FOR INSERT
        WITH CHECK (dmbot_grant_writer() = 'admin');
    CREATE POLICY grant_writer_read ON access_log FOR SELECT
        USING (dmbot_grant_writer() = 'admin');
    """
)

SQL = ACCESS_GRANTS

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # Keyed by Discord account (#771): the person's own grant, or the grant writer.
    "access_grants",
    # Not per person: only the grant writer adds or reads a row (#771).
    "access_log",
)
