"""0069_web_move_codes

The website's door to a move code (#1246).
"""

from __future__ import annotations

# The website's way to a move code (#1246). The website's role has no grant on move_codes or
# account_moves, so it asks through this one door, which does the whole job in one transaction:
# the account must be on a paid plan and not have moved in the last 30 days, the code's hash is
# stored (the earlier code stops working), and the limits are counted here, in the database, so
# every web process agrees and a restart forgets nothing: 3 codes an hour for an account and 5
# an hour for a connection. `src` is a keyed hash (HMAC) of the connection's address: the address
# itself is never stored, and the hash can't be turned back into one without the server's secret.
# The caps match account_move.py's ASKS and MOVE_GAP_S; a test holds them together.
WEB_MOVE_CODES = """
    CREATE TABLE move_code_asks (
        -- Goes with the account (deletion) and follows it through a move.
        user_id  BIGINT NOT NULL CHECK (user_id > 0)
                 REFERENCES web_users ON DELETE CASCADE ON UPDATE CASCADE,
        source   TEXT   NOT NULL CHECK (char_length(source) = 64),
        asked_at BIGINT NOT NULL
    );
    CREATE INDEX move_code_asks_by_user ON move_code_asks (user_id, asked_at);
    CREATE INDEX move_code_asks_by_source ON move_code_asks (source, asked_at);
    CREATE FUNCTION dmbot_web_move_code(who BIGINT, src TEXT, now BIGINT, hash TEXT, ends BIGINT)
        RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        account TEXT;
        ent RECORD;
        outcome TEXT := 'ok';
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        -- One ask at a time for an account, so two at once can't both pass the count.
        SELECT u.dmbot_id INTO account FROM web_users u WHERE u.user_id = who FOR UPDATE;
        IF account IS NULL THEN
            outcome := 'no_account';
        ELSE
            SELECT e.plan, e.status, e.provider_subscription_id INTO ent
                FROM entitlements e WHERE e.user_id = who;
            IF NOT FOUND OR ent.plan = 'try-it' OR ent.status <> 'active'
               OR ent.provider_subscription_id IS NULL THEN
                outcome := 'not_paid';
            ELSIF EXISTS (SELECT 1 FROM account_moves m
                          WHERE m.dmbot_id = account AND m.moved_at > now - 2592000) THEN
                outcome := 'too_soon';
            ELSE
                -- One count at a time for a connection too, so asks from several accounts at
                -- once can't all see "fewer than 5".
                PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.move_ask:' || src, 0));
                DELETE FROM move_code_asks a WHERE a.asked_at <= now - 3600;
                IF (SELECT count(*) FROM move_code_asks a WHERE a.user_id = who) >= 3
                   OR (SELECT count(*) FROM move_code_asks a WHERE a.source = src) >= 5 THEN
                    outcome := 'slow_down';
                ELSE
                    INSERT INTO move_code_asks (user_id, source, asked_at) VALUES (who, src, now);
                    INSERT INTO move_codes (user_id, code_hash, created_at, expires_at)
                        VALUES (who, hash, now, LEAST(ends, now + 1800))
                        ON CONFLICT (user_id) DO UPDATE SET code_hash = EXCLUDED.code_hash,
                            created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at,
                            used_at = NULL;
                END IF;
            END IF;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN outcome;
    END
    $fn$;
    -- The email didn't go, so no code should be waiting that nobody has seen, and the ask
    -- is given back: a failed send must not use up an hour's three. Only that very code is
    -- removed (a newer one asked for meanwhile, and already mailed, must stay); `at` is the
    -- time the ask was made.
    CREATE FUNCTION dmbot_web_cancel_move_code(who BIGINT, hash TEXT, at BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
            DELETE FROM move_codes WHERE user_id = who AND code_hash = hash AND used_at IS NULL;
            DELETE FROM move_code_asks WHERE ctid = (SELECT a.ctid FROM move_code_asks a
                WHERE a.user_id = who AND a.asked_at = at LIMIT 1);
        $fn$;
    -- The hourly sweep: asks older than the window are of no use.
    CREATE FUNCTION dmbot_web_forget_move_asks(now BIGINT) RETURNS BIGINT
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
            WITH gone AS (DELETE FROM move_code_asks WHERE asked_at <= now - 3600 RETURNING 1)
            SELECT count(*) FROM gone
        $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_forget_move_asks(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_forget_move_asks(BIGINT) FROM PUBLIC;
"""

SQL = WEB_MOVE_CODES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A move code asked for on the website (#1246).
    ("dmbot_web_move_code", "dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_cancel_move_code", "dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_forget_move_asks", "dmbot_web_forget_move_asks(BIGINT)"),
)
