"""0065_trial."""

from __future__ import annotations

TRIAL = """
    -- Try It's last-day message and automatic extension (#1229; docs/PLAN.md, "Accounts").
    --   trial_extensions   one row for each 30 days added after Try It ended, so the bot can
    --                      tell the person at their next /dmbot start (told_at) and the next
    --                      extension counts from this one. Ids and times only.
    -- And two doors for the bot's daily job, which acts for no one person: it may list who is
    -- in Try It (with the facts the rules need) and extend one person's Try It. Both are
    -- SECURITY DEFINER, granted to the bot's role only, never the website's.
    -- Like try_it_used, these rows are ids and times only and are not removed with an account:
    -- a person who deletes their account and signs up again is not messaged twice.
    -- (sent_emails also holds the private-message records of these jobs, kinds starting "dm:";
    -- they are not emails and do not count toward the daily cap.)
    CREATE TABLE trial_extensions (
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        extended_at BIGINT NOT NULL,
        told_at     BIGINT,
        PRIMARY KEY (user_id, extended_at)
    );
    -- Like the orphan and pause doors (0037, 0044), the functions open the tables they read to
    -- themselves through a setting they set and put back, and only for this one purpose.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY trial_read ON entitlements FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_update ON entitlements FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''trial'' AND plan = ''try-it'')
                WITH CHECK (dmbot_owner_sync() = ''trial'' AND plan = ''try-it'')',
            current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON web_users FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON owner_hours FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON payment_events FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON access_grants FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_trial_people(since BIGINT, now BIGINT)
        RETURNS TABLE (user_id BIGINT, dmbot_id TEXT, email TEXT, period_end BIGINT,
                       minutes BIGINT, paid BOOLEAN, extensions INTEGER, granted BOOLEAN)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        RETURN QUERY
        SELECT e.user_id, u.dmbot_id, u.email, e.period_end,
               COALESCE((SELECT sum(h.minutes) FROM owner_hours h
                         WHERE h.owner_user_id = e.user_id), 0)::BIGINT,
               EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = e.user_id
                       AND (p.kind IS NULL OR p.kind = ANY(
                            ARRAY['subscription_started', 'subscription_renewed',
                                  'extra_hours_bought']))),
               (SELECT count(*)::INTEGER FROM trial_extensions t WHERE t.user_id = e.user_id),
               EXISTS (SELECT 1 FROM access_grants g WHERE g.discord_user_id = e.user_id
                       AND g.revoked_at IS NULL
                       AND (g.ends_at IS NULL OR g.ends_at > now))
        FROM entitlements e JOIN web_users u ON u.user_id = e.user_id
        WHERE e.plan = 'try-it' AND e.status = 'active' AND e.period_end >= since;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    -- Try It's 8 hours are 8 hours in all: an extension has a cap of 8 hours less the minutes
    -- already used, counted in minutes (`minutes_taken` comes off the cap). The new cap is
    -- worked out here from the meter, whatever the caller thinks.
    ALTER TABLE entitlements ADD COLUMN minutes_taken INTEGER NOT NULL DEFAULT 0
        CHECK (minutes_taken >= 0);
    CREATE FUNCTION dmbot_extend_trial(who BIGINT, now BIGINT, days INTEGER)
        RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        used BIGINT;
        changed INTEGER;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        -- Only a Try It that has ended and is still the person's plan, for 30 days, three
        -- days after the end, for someone who never paid and has used under 4 hours in all.
        IF days <> 30 THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN FALSE;
        END IF;
        SELECT COALESCE(sum(h.minutes), 0) INTO used FROM owner_hours h
            WHERE h.owner_user_id = who;
        UPDATE entitlements
           SET period_start = now, period_end = now + days * 86400, hours_cap = 8,
               minutes_taken = used, extra_hours = 0, plan_changed_at = now, updated_at = now
         WHERE entitlements.user_id = who AND plan = 'try-it' AND status = 'active'
           AND period_end + 259200 <= now AND used < 240
           AND NOT EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = who
                           AND (p.kind IS NULL OR p.kind = ANY(
                                ARRAY['subscription_started', 'subscription_renewed',
                                      'extra_hours_bought'])));
        GET DIAGNOSTICS changed = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        IF changed = 0 THEN
            RETURN FALSE;
        END IF;
        INSERT INTO trial_extensions (user_id, extended_at) VALUES (who, now);
        RETURN TRUE;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_trial_people(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_trial_people(BIGINT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER) FROM PUBLIC;
    """

SQL = TRIAL
