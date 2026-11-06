"""0011_web_accounts."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate_user,
    _setting,
)

WEB_ACCOUNTS = (
    _setting("dmbot_current_user", "dmbot.user_id", "BIGINT")
    + _setting("dmbot_current_session", "dmbot.session", "TEXT")
    + _setting("dmbot_install_guild", "dmbot.install_guild", "BIGINT")
    + _setting("dmbot_plan_writer", "dmbot.plan_writer", "TEXT")
    + _setting("dmbot_cleanup", "dmbot.cleanup", "TEXT")
    + """
    -- Settings, each set only for one transaction by dmbot.db.Database:
    --   dmbot.user_id       the website's signed-in person (Database.user)
    --   dmbot.session       the hash of the session cookie on this request (Database.session):
    --                       knowing the hash means holding the cookie
    --   dmbot.install_guild the one server a person is adding DMbot to or linking
    --                       (Database.user(install_guild=...)); unlocks only `installs`,
    --                       never the server's campaigns
    --   dmbot.plan_writer   set only by Database.plan_writer(): the payment webhook and
    --                       Try It, the only code allowed to change `entitlements`
    --   dmbot.cleanup       set only by Database.cleanup(): the website's hourly sweep
    --                       ('expired-sessions', and 'old-feedback' since 0026, #665)

    CREATE FUNCTION dmbot_now() RETURNS BIGINT
        LANGUAGE sql STABLE
        AS $fn$ SELECT extract(epoch FROM now())::BIGINT $fn$;

    -- A person who signed in on the website with Discord. Email is what Discord gives us
    -- (scope `email`), used only for account and plan messages (privacy page, #433).
    CREATE TABLE web_users (
        user_id            BIGINT PRIMARY KEY,
        email              TEXT,
        created_at         BIGINT NOT NULL,
        last_sign_in_at    BIGINT NOT NULL
    );

    -- Discord users who have started Try It, so it can be started only once (#435).
    -- Only the plan writer adds a row; nobody changes or removes one, and it stays after
    -- the account is deleted (it holds nothing but the Discord id; 0017 dropped the
    -- date), so deleting and signing up again doesn't give a second free month.
    CREATE TABLE try_it_used (
        user_id  BIGINT PRIMARY KEY,
        used_at  BIGINT NOT NULL
    );

    -- What each person has paid for, one row per person. Changed only by the plan writer
    -- (the payment webhook and Try It, dmbot.web, #435), which the database enforces
    -- with the dmbot.plan_writer setting; everyone else, including the bot's plan rules
    -- (#437), only reads it. `plan` matches plans.json. Caps are copied in when the plan
    -- is written, so a later price-list change never alters a plan someone bought.
    -- The payment company's events carry our Discord user id (checkout passes it as
    -- custom data), which is how the webhook finds the person.
    CREATE TABLE entitlements (
        user_id          BIGINT PRIMARY KEY REFERENCES web_users ON DELETE CASCADE,
        plan             TEXT NOT NULL
            CHECK (plan IN ('try-it', 'table', 'two-tables', 'guild', 'pro')),
        status           TEXT NOT NULL CHECK (status IN ('active', 'grace', 'lapsed')),
        hours_cap        INTEGER NOT NULL CHECK (hours_cap >= 0),
        -- Extra hours bought for the current period (+10 each, #432); back to 0 when a
        -- new period starts, so a renewal never wipes or keeps them by accident.
        extra_hours      INTEGER NOT NULL DEFAULT 0 CHECK (extra_hours >= 0),
        campaign_cap     INTEGER NOT NULL CHECK (campaign_cap >= 0),
        -- The billing period the hours belong to (Unix seconds).
        period_start     BIGINT NOT NULL,
        period_end       BIGINT NOT NULL CHECK (period_end > period_start),
        -- Status "grace": a failed payment must be fixed by then (7 days, #437).
        grace_ends_at    BIGINT,
        -- When the plan stopped (status "lapsed"): the 120-day retention counts from here.
        lapsed_at        BIGINT,
        -- When the plan last changed: on a downgrade, the first campaigns started after
        -- this stay active (#437).
        plan_changed_at  BIGINT NOT NULL,
        provider         TEXT NOT NULL,
        -- The payment company's own ids, for its customer page. Never card data.
        provider_customer_id      TEXT,
        provider_subscription_id  TEXT,
        -- The newest event applied, so an older event arriving late can't undo it.
        last_event_at    BIGINT NOT NULL,
        updated_at       BIGINT NOT NULL,
        CHECK ((status = 'grace') = (grace_ends_at IS NOT NULL)),
        CHECK ((status = 'lapsed') = (lapsed_at IS NOT NULL))
    );

    -- Payment events already applied, so a repeated delivery changes nothing (#435).
    -- Idempotency: INSERT ... ON CONFLICT DO NOTHING RETURNING in the same transaction as
    -- the entitlements change (the primary key spans everyone, even though each person
    -- sees only their own rows). Only the plan writer adds a row; nobody changes or
    -- removes one, so an event can never be applied twice. Ids only, no payment details.
    -- Events that name no person are not recorded here.
    CREATE TABLE payment_events (
        provider     TEXT NOT NULL,
        event_id     TEXT NOT NULL,
        user_id      BIGINT NOT NULL,
        received_at  BIGINT NOT NULL,
        PRIMARY KEY (provider, event_id)
    );
    CREATE INDEX payment_events_by_user ON payment_events (user_id);

    -- Website sessions. The cookie holds a random token; only its SHA-256 is stored, so a
    -- database leak can't be used to sign in. The Discord token is never stored: the
    -- servers Discord listed at sign-in are kept here, for this session only. An expired
    -- session can't be found by its hash; Database.cleanup() deletes expired rows.
    CREATE TABLE web_sessions (
        id_hash     TEXT PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES web_users ON DELETE CASCADE,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL,
        -- [{"id": "...", "name": "...", "manage": true}], from the `guilds` scope.
        guilds      JSONB NOT NULL DEFAULT '[]'
    );
    CREATE INDEX web_sessions_by_user ON web_sessions (user_id);
    CREATE INDEX web_sessions_expiry ON web_sessions (expires_at);

    -- Who added DMbot to which server (owner decision on #435). `via`: 'site' when added
    -- through the website's install button (the person is known), 'link' when the bot
    -- joined through a plain invite link (the person fills in later with "Link this
    -- server", after the web API checks they manage the server). Visible to that server
    -- and to the person who installed it; written only for the server being installed.
    CREATE TABLE installs (
        guild_id              BIGINT PRIMARY KEY,
        installed_by_user_id  BIGINT REFERENCES web_users ON DELETE SET NULL,
        installed_at          BIGINT NOT NULL,
        -- How DMbot arrived. The installer is empty for a link until someone links the
        -- server, and becomes empty again if that person deletes their account.
        via                   TEXT NOT NULL CHECK (via IN ('site', 'link'))
    );
    CREATE INDEX installs_by_user ON installs (installed_by_user_id)
        WHERE installed_by_user_id IS NOT NULL;
    """
    + _isolate_user("web_users")
    + """
    -- try_it_used and payment_events: the person reads their own rows; only the plan
    -- writer adds one; no policy allows changing or removing a row.
    ALTER TABLE try_it_used ENABLE ROW LEVEL SECURITY;
    ALTER TABLE try_it_used FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON try_it_used FOR SELECT
        USING (user_id = dmbot_current_user());
    CREATE POLICY plan_writer_insert ON try_it_used FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');

    ALTER TABLE payment_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE payment_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON payment_events FOR SELECT
        USING (user_id = dmbot_current_user());
    CREATE POLICY plan_writer_insert ON payment_events FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');

    -- entitlements: the person reads their own row; only the plan writer may change it.
    ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
    ALTER TABLE entitlements FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON entitlements FOR SELECT
        USING (user_id = dmbot_current_user());
    CREATE POLICY plan_writer_insert ON entitlements FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');
    CREATE POLICY plan_writer_update ON entitlements FOR UPDATE
        USING (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments')
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');
    CREATE POLICY plan_writer_delete ON entitlements FOR DELETE
        USING (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');

    -- web_sessions: the person's own sessions, or the one session whose cookie was shown
    -- (not expired). The cookie alone can read its session, never change who owns it.
    -- The cleanup sweep sees and deletes expired sessions only.
    ALTER TABLE web_sessions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE web_sessions FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON web_sessions
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());
    CREATE POLICY by_cookie ON web_sessions FOR SELECT
        USING (id_hash = dmbot_current_session() AND expires_at > dmbot_now());
    CREATE POLICY cleanup_read ON web_sessions FOR SELECT
        USING (dmbot_cleanup() = 'expired-sessions' AND expires_at <= dmbot_now());
    CREATE POLICY cleanup_delete ON web_sessions FOR DELETE
        USING (dmbot_cleanup() = 'expired-sessions' AND expires_at <= dmbot_now());

    -- installs: seen by its server and by its installer. The bot writes its own server's
    -- row (Database.guild). The website writes only the install server's row, only naming
    -- the signed-in person, and never takes over a row someone else installed or linked.
    -- Only the bot removes a row.
    ALTER TABLE installs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE installs FORCE ROW LEVEL SECURITY;
    CREATE POLICY install_read ON installs FOR SELECT
        USING (guild_id = dmbot_current_guild()
               OR guild_id = dmbot_install_guild()
               OR installed_by_user_id = dmbot_current_user());
    CREATE POLICY install_insert ON installs FOR INSERT
        WITH CHECK (guild_id = dmbot_current_guild()
                    OR (guild_id = dmbot_install_guild()
                        AND installed_by_user_id = dmbot_current_user()));
    CREATE POLICY install_update ON installs FOR UPDATE
        USING (guild_id = dmbot_current_guild()
               OR (guild_id = dmbot_install_guild()
                   AND (installed_by_user_id IS NULL
                        OR installed_by_user_id = dmbot_current_user())))
        WITH CHECK (guild_id = dmbot_current_guild()
                    OR (guild_id = dmbot_install_guild()
                        AND installed_by_user_id = dmbot_current_user()));
    CREATE POLICY install_delete ON installs FOR DELETE
        USING (guild_id = dmbot_current_guild());
    """
)

SQL = WEB_ACCOUNTS

USER_ISOLATED_TABLES: tuple[str, ...] = (
    "web_users",
    "try_it_used",
    "entitlements",
    "payment_events",
    "web_sessions",
    "installs",
)
