"""0032_usage."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _setting,
)

USAGE = (
    _setting("dmbot_meter", "dmbot.meter", "TEXT")
    + """
    -- The hours meter (#437 part 2; docs/PLAN.md, "Plans and pricing"): DMbot's listening
    -- minutes, in two tables so no query ever crosses servers.
    --
    -- session_usage: per campaign and session, in that server's own scope and deleted
    -- with the campaign. One row per owner a session had: if the campaign is handed over
    -- mid-session, the minutes after the hand-over are recorded for the new owner.
    CREATE TABLE session_usage (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        session_started_at BIGINT NOT NULL,
        -- 0: nobody owned the campaign yet; those minutes are kept here so they are
        -- never billed to whoever takes it on later, and never reach owner_hours.
        owner_user_id      BIGINT NOT NULL CHECK (owner_user_id >= 0),
        minutes            INTEGER NOT NULL DEFAULT 0 CHECK (minutes >= 0),
        updated_at         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_started_at, owner_user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("session_usage")
    + """
    -- owner_hours: an owner's minutes per plan month, summed over every campaign they own,
    -- in any server. Numbers only: no server, campaign or name, so reading it reveals
    -- nothing about where the hours went. It is scoped to the owner (like entitlements),
    -- and only the bot's meter (dmbot.meter, set by Database.meter(), which also sets
    -- the server being played in) may add to it; the owner may read their own row.
    CREATE TABLE owner_hours (
        owner_user_id BIGINT NOT NULL CHECK (owner_user_id > 0),
        month_start   BIGINT NOT NULL,
        minutes       INTEGER NOT NULL DEFAULT 0 CHECK (minutes >= 0),
        PRIMARY KEY (owner_user_id, month_start)
    );
    ALTER TABLE owner_hours ENABLE ROW LEVEL SECURITY;
    ALTER TABLE owner_hours FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON owner_hours FOR SELECT
        USING (owner_user_id = dmbot_current_user());
    CREATE POLICY meter_insert ON owner_hours FOR INSERT
        WITH CHECK (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot');
    CREATE POLICY meter_update ON owner_hours FOR UPDATE
        USING (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot')
        WITH CHECK (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot');
    """
)

SQL = USAGE

ISOLATED_TABLES: tuple[str, ...] = ("session_usage",)

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # An owner's hours per plan month; only the meter adds to it (#437).
    "owner_hours",
)
