"""0068_moves

0066 is the sign-in-by-email migration (#1237).
"""

from __future__ import annotations

MOVES = """
    -- Moving an account to a new Discord account (#1230; docs/PLAN.md, "Accounts"). A paid
    -- account may move once in 30 days; the plan, its hours, the Stripe link, the campaigns it
    -- owns and its place in them go to the new Discord ID in ONE transaction. The DMbot ID
    -- stays. Recording consent does not move.
    --   move_codes      the one move code an account has asked for: only its hash, when it
    --                   was made and runs out, and when it was used
    --   account_moves   the "moved" marker: which Discord ID moved to which, when, and the
    --                   DMbot ID that went with it; told_at is when the old account was
    --                   told (the first time it uses DMbot again). Unscoped like try_it_used.
    CREATE TABLE move_codes (
        user_id    BIGINT PRIMARY KEY CHECK (user_id > 0),
        code_hash  TEXT NOT NULL UNIQUE CHECK (char_length(code_hash) = 64),
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        used_at    BIGINT
    );
    CREATE TABLE account_moves (
        old_user_id BIGINT PRIMARY KEY CHECK (old_user_id > 0),
        new_user_id BIGINT NOT NULL CHECK (new_user_id > 0),  -- not unique: A to B to C to B
        dmbot_id    TEXT NOT NULL,
        moved_at    BIGINT NOT NULL,
        told_at     BIGINT,
        -- The payment company's ids at the move, so the website's job can point them at the
        -- new Discord ID after the commit (stripe_done_at NULL = still to do).
        customer_id        TEXT,
        subscription_id    TEXT,
        stripe_done_at     BIGINT,
        -- Backoff for the update, kept here so it survives a restart and every web process
        -- agrees: tries so far, and when the last one was (database time).
        stripe_attempts    INTEGER NOT NULL DEFAULT 0,
        stripe_last_try_at BIGINT
    );
    CREATE INDEX account_moves_by_id ON account_moves (dmbot_id, moved_at);
    CREATE INDEX account_moves_by_new ON account_moves (new_user_id);
    -- Every Discord account a DMbot account has used (owner request, 2026-10-14): ONE row for
    -- each move, written inside dmbot_move_account in the same transaction, and never
    -- changed. The only deletion is the account's own deletion (dmbot_forget_account_history).
    -- email_used is the payment email at the time of the move (where the move code went).
    -- account_moves stays the routing marker; this is the record. Row-level security by
    -- person: a row is visible to the account that holds its DMbot ID; the website reads
    -- through dmbot_previous_ids(), and has no grant on the table.
    CREATE TABLE account_id_history (
        id          BIGSERIAL PRIMARY KEY,
        dmbot_id    TEXT   NOT NULL,
        old_user_id BIGINT NOT NULL CHECK (old_user_id > 0),
        new_user_id BIGINT NOT NULL CHECK (new_user_id > 0),
        moved_at    BIGINT NOT NULL,
        email_used  TEXT
    );
    CREATE INDEX account_id_history_by_id ON account_id_history (dmbot_id, id);
    ALTER TABLE account_id_history ENABLE ROW LEVEL SECURITY;
    ALTER TABLE account_id_history FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON account_id_history FOR SELECT
        USING (dmbot_id IN (SELECT u.dmbot_id FROM web_users u
                            WHERE u.user_id = dmbot_current_user()));
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY move_insert ON account_id_history FOR INSERT TO %I'
            ' WITH CHECK (dmbot_owner_sync() = ''move'')',
            current_user);
        EXECUTE format(
            'CREATE POLICY forget_delete ON account_id_history FOR DELETE TO %I'
            ' USING (dmbot_owner_sync() = ''forget_history'')',
            current_user);
    END
    $do$;
    CREATE INDEX account_moves_to_sync ON account_moves (moved_at) WHERE stripe_done_at IS NULL;
    -- Rows that point at the account follow its Discord ID when the move changes it.
    ALTER TABLE entitlements DROP CONSTRAINT entitlements_user_id_fkey,
        ADD CONSTRAINT entitlements_user_id_fkey FOREIGN KEY (user_id)
            REFERENCES web_users ON DELETE CASCADE ON UPDATE CASCADE;
    ALTER TABLE installs DROP CONSTRAINT installs_installed_by_user_id_fkey,
        ADD CONSTRAINT installs_installed_by_user_id_fkey FOREIGN KEY (installed_by_user_id)
            REFERENCES web_users ON DELETE SET NULL ON UPDATE CASCADE;
    -- A move changes the Discord ID on the account's own row (the DMbot ID stays), so the
    -- unique check on the DMbot ID is never in play. Only the move may do that; any other
    -- change of either still fails. An old Discord ID that moved away can't make a new
    -- account on the website: its DMbot ID is the moved account's.
    -- (SECURITY DEFINER: it reads account_moves, which the website's role can't.)
    CREATE OR REPLACE FUNCTION dmbot_web_users_id() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF EXISTS (SELECT 1 FROM account_moves WHERE old_user_id = NEW.user_id) THEN
                RAISE EXCEPTION 'This account moved'
                    USING ERRCODE = 'P0001', HINT = 'account_moved';
            END IF;
            NEW.dmbot_id := dmbot_base36(NEW.user_id);
        ELSIF NEW.dmbot_id IS DISTINCT FROM OLD.dmbot_id THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        ELSIF NEW.user_id <> OLD.user_id AND dmbot_owner_sync() <> 'move' THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        END IF;
        RETURN NEW;
    END
    $fn$;
    -- The move door opens the tables it touches to itself through a setting it sets and puts
    -- back (like the orphan and Try It doors). One policy for each table, for the function's
    -- owner only.
    DO $do$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY['web_users', 'entitlements', 'web_sessions', 'installs',
                                 'payment_events', 'owner_hours', 'session_usage',
                                 'try_it_used', 'campaigns', 'campaign_dms',
                                 'memory_entities', 'active_sessions',
                                 'campaign_handover_offers']
        LOOP
            EXECUTE format(
                'CREATE POLICY move_all ON %I FOR ALL TO %I
                    USING (dmbot_owner_sync() = ''move'')
                    WITH CHECK (dmbot_owner_sync() = ''move'')',
                t, current_user);
        END LOOP;
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_users_id() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    CREATE FUNCTION dmbot_move_account(old BIGINT, new BIGINT, now BIGINT, which TEXT)
        RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        acct RECORD;
        ent RECORD;
        owned TEXT[];
        outcome TEXT := 'moved';
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        IF old IS NULL OR new IS NULL OR old <= 0 OR new <= 0 OR old = new THEN
            outcome := 'same';
        END IF;
        -- Same lock order as the hand-over and the pause door: the campaigns (in a fixed
        -- order), then the people. A payment for either account waits for the move.
        IF old IS NOT NULL AND new IS NOT NULL AND old > 0 AND new > 0 AND old <> new THEN
            PERFORM 1 FROM campaigns c WHERE c.owner_user_id IN (old, new)
                ORDER BY c.guild_id, c.id FOR UPDATE;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.owner_slots:' || least(old, new)::text, 0));
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.owner_slots:' || greatest(old, new)::text, 0));
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.plan_writer:' || least(old, new)::text, 0));
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.plan_writer:' || greatest(old, new)::text, 0));
        END IF;
        IF outcome = 'moved' THEN
            SELECT * INTO acct FROM web_users WHERE user_id = old FOR UPDATE;
            IF NOT FOUND THEN
                outcome := 'no_account';
            END IF;
        END IF;
        IF outcome = 'moved' THEN
            SELECT * INTO ent FROM entitlements WHERE user_id = old FOR UPDATE;
            IF NOT FOUND OR ent.plan = 'try-it' OR ent.status <> 'active'
               OR ent.provider_subscription_id IS NULL THEN
                outcome := 'not_paid';
            END IF;
        END IF;
        IF outcome <> 'moved' THEN  -- nothing below reads a record that was never found
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN outcome;
        END IF;
        -- The code is checked and spent here, in the same transaction as the move, so two
        -- moves with one code can't both happen.
        IF outcome = 'moved' THEN
            PERFORM 1 FROM move_codes m WHERE m.user_id = old AND m.code_hash = which
                AND m.used_at IS NULL AND m.expires_at > now FOR UPDATE;
            IF NOT FOUND THEN
                outcome := 'bad_code';
            END IF;
        END IF;
        IF outcome <> 'moved' THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN outcome;
        END IF;
        IF outcome = 'moved' AND EXISTS (
                SELECT 1 FROM active_sessions a JOIN campaigns c
                    ON c.id = a.campaign_id AND c.guild_id = a.guild_id
                WHERE c.owner_user_id = new) THEN
            outcome := 'target_listening';
        END IF;
        IF outcome = 'moved' AND EXISTS (SELECT 1 FROM account_moves
                                         WHERE dmbot_id = acct.dmbot_id
                                           AND moved_at > now - 30 * 86400) THEN
            outcome := 'too_soon';
        END IF;
        IF outcome = 'moved' AND EXISTS (SELECT 1 FROM entitlements
                                         WHERE user_id = new AND plan <> 'try-it'
                                           AND status <> 'lapsed') THEN
            outcome := 'new_has_plan';
        END IF;
        -- The target must be a fresh account, or the account this one came from (moving back).
        -- Any plan it ever had, or being the target of an earlier move, is its own history.
        IF outcome = 'moved' AND (
               EXISTS (SELECT 1 FROM entitlements e WHERE e.user_id = new AND e.plan <> 'try-it')
            OR EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = new
                       AND (p.kind IS NULL OR p.kind = ANY(
                            ARRAY['subscription_started', 'subscription_renewed',
                                  'extra_hours_bought'])))
            OR EXISTS (SELECT 1 FROM account_moves m
                       WHERE m.new_user_id = new AND m.dmbot_id <> acct.dmbot_id)
            OR EXISTS (SELECT 1 FROM account_moves m
                       WHERE m.old_user_id = new AND m.dmbot_id <> acct.dmbot_id)) THEN
            outcome := 'target_history';
        END IF;
        IF outcome = 'moved' AND EXISTS (
                SELECT 1 FROM active_sessions a JOIN campaigns c
                    ON c.id = a.campaign_id AND c.guild_id = a.guild_id
                WHERE c.owner_user_id = old) THEN
            outcome := 'listening';
        END IF;
        IF outcome = 'moved' AND (SELECT count(*) FROM campaigns
                                  WHERE owner_user_id IN (old, new)) > ent.campaign_cap THEN
            outcome := 'too_many_campaigns';
        END IF;
        IF outcome <> 'moved' THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN outcome;
        END IF;

        SELECT COALESCE(array_agg(id), '{}') INTO owned FROM campaigns WHERE owner_user_id = old;
        -- Whatever the new account had of its own that is not a paid plan goes: a Try It
        -- or a stopped plan, and its sign-ins.
        DELETE FROM entitlements WHERE user_id = new;
        DELETE FROM web_users WHERE user_id = new;
        DELETE FROM web_sessions WHERE user_id = old;
        -- The account's own row takes the new Discord ID; its plan and installs follow it.
        UPDATE web_users SET user_id = new WHERE user_id = old;
        UPDATE payment_events SET user_id = new WHERE user_id = old;

        -- The new account's own Try It minutes don't count toward the paid plan: they go.
        DELETE FROM owner_hours WHERE owner_user_id = new;
        INSERT INTO owner_hours (owner_user_id, month_start, minutes, grace_session)
            SELECT new, month_start, minutes, grace_session FROM owner_hours
             WHERE owner_user_id = old
            ON CONFLICT (owner_user_id, month_start)
            DO UPDATE SET minutes = owner_hours.minutes + EXCLUDED.minutes;
        DELETE FROM owner_hours WHERE owner_user_id = old;
        UPDATE session_usage SET owner_user_id = new WHERE owner_user_id = old;

        -- An offer to hand a campaign over, to or from the old account, ends with the move.
        UPDATE campaign_handover_offers SET status = 'withdrawn', decided_at = now
            WHERE status = 'open' AND (from_user_id = old OR to_user_id = old
                                       OR from_user_id = new OR to_user_id = new);
        UPDATE campaigns SET owner_user_id = new WHERE owner_user_id = old;
        DELETE FROM campaign_dms d WHERE d.user_id = old AND d.campaign_id = ANY(owned)
            AND EXISTS (SELECT 1 FROM campaign_dms n
                        WHERE n.campaign_id = d.campaign_id AND n.user_id = new);
        UPDATE campaign_dms SET user_id = new WHERE user_id = old AND campaign_id = ANY(owned);
        UPDATE memory_entities e SET played_by = new
            WHERE e.played_by = old AND e.campaign_id = ANY(owned)
              AND NOT EXISTS (SELECT 1 FROM memory_entities x
                              WHERE x.guild_id = e.guild_id AND x.campaign_id = e.campaign_id
                                AND x.type = 'person' AND x.played_by = new
                                AND x.status IN ('proposed', 'confirmed'));

        -- One free trial for each person, and a moved ID can never start another.
        INSERT INTO try_it_used (user_id) VALUES (old), (new) ON CONFLICT DO NOTHING;
        DELETE FROM move_codes WHERE user_id = old;
        -- "No offer emails" is about the person, so it goes with the account.
        INSERT INTO email_unsubscribed (user_id, at)
            SELECT new, at FROM email_unsubscribed WHERE user_id = old
            ON CONFLICT DO NOTHING;
        -- Moving back: the earlier marker for this Discord ID ends, it is the account again.
        DELETE FROM account_moves WHERE old_user_id = new;
        INSERT INTO account_id_history (dmbot_id, old_user_id, new_user_id, moved_at, email_used)
            VALUES (acct.dmbot_id, old, new, now, acct.payment_email);
        INSERT INTO account_moves (old_user_id, new_user_id, dmbot_id, moved_at, customer_id,
                                   subscription_id, stripe_done_at)
            VALUES (old, new, acct.dmbot_id, now, ent.provider_customer_id,
                    ent.provider_subscription_id,
                    CASE WHEN ent.provider_customer_id IS NULL
                          AND ent.provider_subscription_id IS NULL THEN now END);
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN 'moved';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT) FROM PUBLIC;
    -- Two small doors for the website's role, which has no grant on account_moves:
    --   dmbot_user_of(id)   the Discord ID that holds a DMbot ID now (an unsubscribe or
    --                       personal link carries the ID the account was made with, and the
    --                       account may have moved since), or no row
    --   dmbot_moved_to(old, subscription, customer)
    --                       the Discord ID a moved account's plan went to, only when that
    --                       plan is the one the payment event names, so a plan the old
    --                       account buys later is never sent to the new one
    CREATE FUNCTION dmbot_user_of(id TEXT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        found BIGINT;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        SELECT u.user_id INTO found FROM web_users u WHERE u.dmbot_id = id;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN found;
    END
    $fn$;
    CREATE FUNCTION dmbot_moved_to(old BIGINT, sub TEXT, cust TEXT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        found BIGINT;
        here BIGINT := old;
        hop BIGINT;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        -- Follow the moves (a few at most) to the account that holds the plan now.
        FOR i IN 1..5 LOOP
            SELECT m.new_user_id INTO hop FROM account_moves m WHERE m.old_user_id = here;
            EXIT WHEN hop IS NULL;
            here := hop;
            IF EXISTS (SELECT 1 FROM entitlements e
                       WHERE e.user_id = here
                         AND ((sub IS NOT NULL AND e.provider_subscription_id = sub)
                              OR (cust IS NOT NULL AND e.provider_customer_id = cust))) THEN
                found := here;
                EXIT;
            END IF;
            hop := NULL;
        END LOOP;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_user_of(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_moved_to(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_user_of(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_moved_to(BIGINT, TEXT, TEXT) FROM PUBLIC;
    -- The payment company's records name the Discord ID the plan was bought with. After a
    -- move commits, the website's job points them at the new one: these two doors say which
    -- moves still need it, and that one is done (nothing else of account_moves is readable).
    CREATE FUNCTION dmbot_moves_to_sync()
        RETURNS TABLE (old_user_id BIGINT, new_user_id BIGINT, customer_id TEXT,
                       subscription_id TEXT)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        -- Never tried first, then the longest ago; one that failed waits a minute, two,
        -- four... up to an hour, so a few stuck moves can't crowd out a new one.
        SELECT m.old_user_id, m.new_user_id, m.customer_id, m.subscription_id
            FROM account_moves m
            WHERE m.stripe_done_at IS NULL
              AND (m.stripe_last_try_at IS NULL
                   OR m.stripe_last_try_at + LEAST(60 * (2 ^ LEAST(m.stripe_attempts, 6))::BIGINT,
                                                   3600) <= extract(epoch FROM now())::BIGINT)
            ORDER BY m.stripe_last_try_at NULLS FIRST, m.moved_at
            LIMIT 20;
    $fn$;
    CREATE FUNCTION dmbot_move_failed(who BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE account_moves SET stripe_attempts = stripe_attempts + 1,
                                 stripe_last_try_at = extract(epoch FROM now())::BIGINT
            WHERE old_user_id = who AND stripe_done_at IS NULL;
    $fn$;
    CREATE FUNCTION dmbot_move_synced(who BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE account_moves SET stripe_done_at = extract(epoch FROM now())::BIGINT
            WHERE old_user_id = who AND stripe_done_at IS NULL;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_moves_to_sync() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_synced(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_failed(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_moves_to_sync() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_move_synced(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_move_failed(BIGINT) FROM PUBLIC;
    -- The signed-in person's earlier Discord accounts, oldest first, for the data export. The
    -- function runs as the table's owner, whose row-level security is the same person-only
    -- policy, so it can only ever return the caller's own rows.
    CREATE FUNCTION dmbot_previous_ids()
        RETURNS TABLE (old_user_id BIGINT, moved_at BIGINT, email_used TEXT)
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT h.old_user_id, h.moved_at, h.email_used FROM account_id_history h ORDER BY h.id;
    $fn$;
    -- Account deletion takes the history with it: the person's own DMbot ID only.
    CREATE FUNCTION dmbot_forget_account_history() RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        mine TEXT;
        gone INTEGER;
    BEGIN
        SELECT u.dmbot_id INTO mine FROM web_users u WHERE u.user_id = dmbot_current_user();
        IF mine IS NULL THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_history', true);
        DELETE FROM account_id_history h WHERE h.dmbot_id = mine;
        GET DIAGNOSTICS gone = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_previous_ids() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_account_history()'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_previous_ids() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_forget_account_history() FROM PUBLIC;
    -- What a move would take, for the confirmation screen: the plan and the name of each
    -- campaign. Names only; nothing else of a campaign leaves.
    CREATE FUNCTION dmbot_move_preview(old BIGINT) RETURNS TABLE (kind TEXT, label TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        RETURN QUERY SELECT 'plan'::TEXT, e.plan FROM entitlements e WHERE e.user_id = old;
        RETURN QUERY SELECT 'campaign'::TEXT, c.name FROM campaigns c
            WHERE c.owner_user_id = old ORDER BY c.name, c.id;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_preview(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_move_preview(BIGINT) FROM PUBLIC;
    """

SQL = MOVES

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # Every Discord account a DMbot account used; keyed by its DMbot ID (#1230).
    "account_id_history",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_user_of", "dmbot_user_of(TEXT)"),  # links made before a move (#1230)
    ("dmbot_moved_to", "dmbot_moved_to(BIGINT, TEXT, TEXT)"),  # payments after a move (#1230)
    ("dmbot_moves_to_sync", "dmbot_moves_to_sync()"),  # the job that updates Stripe (#1230)
    ("dmbot_move_synced", "dmbot_move_synced(BIGINT)"),
    ("dmbot_move_failed", "dmbot_move_failed(BIGINT)"),
    ("dmbot_previous_ids", "dmbot_previous_ids()"),  # the export's earlier Discord accounts
    ("dmbot_forget_account_history", "dmbot_forget_account_history()"),  # account deletion
)
