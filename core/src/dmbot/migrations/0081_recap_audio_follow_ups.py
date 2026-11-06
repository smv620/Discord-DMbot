"""0081_recap_audio_follow_ups

Move + stray sweep (#1399).
"""

from __future__ import annotations

RECAP_AUDIO_FOLLOW_UPS = """
    -- Follow-ups to the recap audio add-on (#1399).
    -- 1. A move retargets the add-on's subscription too. The move records the add-on's
    --    subscription id next to the plan's (a trigger fills it in from the account's row, which
    --    has already followed the move when the marker is added), and the job that updates the
    --    payment company is told about it.
    ALTER TABLE account_moves ADD COLUMN addon_subscription_id TEXT;
    CREATE FUNCTION dmbot_move_addon() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        SELECT e.recap_audio_subscription_id INTO NEW.addon_subscription_id
            FROM entitlements e WHERE e.user_id = NEW.new_user_id;
        IF NEW.addon_subscription_id IS NOT NULL THEN
            NEW.stripe_done_at := NULL;  -- there is a record at the company still to point
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER account_moves_addon BEFORE INSERT ON account_moves
        FOR EACH ROW EXECUTE FUNCTION dmbot_move_addon();
    DROP FUNCTION dmbot_moves_to_sync();
    CREATE FUNCTION dmbot_moves_to_sync()
        RETURNS TABLE (old_user_id BIGINT, new_user_id BIGINT, customer_id TEXT,
                       subscription_id TEXT, addon_subscription_id TEXT)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        -- Never tried first, then the longest ago; one that failed waits a minute, two,
        -- four... up to an hour, so a few stuck moves can't crowd out a new one.
        SELECT m.old_user_id, m.new_user_id, m.customer_id, m.subscription_id,
               m.addon_subscription_id
            FROM account_moves m
            WHERE m.stripe_done_at IS NULL
              AND (m.stripe_last_try_at IS NULL
                   OR m.stripe_last_try_at + LEAST(60 * (2 ^ LEAST(m.stripe_attempts, 6))::BIGINT,
                                                   3600) <= extract(epoch FROM now())::BIGINT)
            ORDER BY m.stripe_last_try_at NULLS FIRST, m.moved_at
            LIMIT 20;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_moves_to_sync() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_addon() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_moves_to_sync() FROM PUBLIC;
    -- 2. An add-on subscription that must not exist and could not be cancelled is kept here, so
    --    the hourly sweep tries again. It holds only the company's subscription id (no person's
    --    data); the website reaches it through four doors, never the table.
    CREATE TABLE recap_audio_strays (
        subscription_id TEXT PRIMARY KEY
                        CHECK (subscription_id ~ '^sub_[A-Za-z0-9_]{1,90}$'),  -- a company id only
        found_at        BIGINT NOT NULL,
        attempts        INTEGER NOT NULL DEFAULT 0,
        last_try_at     BIGINT
    );
    CREATE FUNCTION dmbot_note_stray_addon(sub TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        INSERT INTO recap_audio_strays (subscription_id, found_at, attempts, last_try_at)
            VALUES (sub, extract(epoch FROM now())::BIGINT, 1, extract(epoch FROM now())::BIGINT)
            ON CONFLICT (subscription_id) DO UPDATE SET attempts = recap_audio_strays.attempts + 1,
                last_try_at = EXCLUDED.last_try_at;
    $fn$;
    CREATE FUNCTION dmbot_strays_to_cancel() RETURNS TABLE (subscription_id TEXT, attempts INTEGER)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        -- At most once an hour each, oldest first, a few at a time.
        SELECT s.subscription_id, s.attempts FROM recap_audio_strays s
            WHERE s.last_try_at IS NULL
               OR s.last_try_at + 3600 <= extract(epoch FROM now())::BIGINT
            ORDER BY s.found_at LIMIT 20;
    $fn$;
    CREATE FUNCTION dmbot_stray_addon_tried(sub TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE recap_audio_strays SET attempts = attempts + 1,
            last_try_at = extract(epoch FROM now())::BIGINT WHERE subscription_id = sub;
    $fn$;
    CREATE FUNCTION dmbot_stray_addon_done(sub TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        DELETE FROM recap_audio_strays WHERE subscription_id = sub;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_note_stray_addon(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_strays_to_cancel() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_stray_addon_tried(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_stray_addon_done(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_note_stray_addon(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_strays_to_cancel() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_stray_addon_tried(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_stray_addon_done(TEXT) FROM PUBLIC;
    """

SQL = RECAP_AUDIO_FOLLOW_UPS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Add-on subscriptions that must be cancelled, kept until the company says yes (#1399).
    ("dmbot_note_stray_addon", "dmbot_note_stray_addon(TEXT)"),
    ("dmbot_strays_to_cancel", "dmbot_strays_to_cancel()"),
    ("dmbot_stray_addon_tried", "dmbot_stray_addon_tried(TEXT)"),
    ("dmbot_stray_addon_done", "dmbot_stray_addon_done(TEXT)"),
)
