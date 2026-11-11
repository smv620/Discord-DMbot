"""0430_forget_hours

Deleting an account takes its Discord ID out of the listening hours (#4132), as 0425 did for the AI
credits: `dmbot_forget_hours()`, called from `accounts.delete_person`.
"""

from __future__ import annotations

# `owner_hours.owner_user_id` and `session_usage.owner_user_id` are the person's Discord ID, and
# neither was touched by account deletion. The month totals go. A campaign's `session_usage` rows
# stay (the campaign's own record of how long it was listened to) with the owner set to 0, which
# already means "nobody owned the campaign yet" (0032): those minutes are never billed to anyone,
# also not to whoever takes the campaign on later. That is on purpose.
# If the session already has a row for owner 0 the minutes are added to it (the key is the session
# and the owner, so two rows would collide).
# The policies that allow this are for the function's owner only and open only while the function
# runs (its own `dmbot.owner_sync` value, as the history forget and 0425 do), so no ordinary query
# of the bot can see or change another server's rows through them.
SQL = r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY forget_delete ON owner_hours FOR DELETE TO %I
                USING (dmbot_owner_sync() = ''forget_hours''
                       AND owner_user_id = dmbot_current_user())',
            current_user);
        EXECUTE format(
            'CREATE POLICY forget_read ON session_usage FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''forget_hours''
                       AND (owner_user_id = dmbot_current_user() OR owner_user_id = 0))',
            current_user);
        EXECUTE format(
            'CREATE POLICY forget_insert ON session_usage FOR INSERT TO %I
                WITH CHECK (dmbot_owner_sync() = ''forget_hours'' AND owner_user_id = 0)',
            current_user);
        EXECUTE format(
            'CREATE POLICY forget_update ON session_usage FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''forget_hours'' AND owner_user_id = 0)
                WITH CHECK (dmbot_owner_sync() = ''forget_hours'' AND owner_user_id = 0)',
            current_user);
        EXECUTE format(
            'CREATE POLICY forget_delete ON session_usage FOR DELETE TO %I
                USING (dmbot_owner_sync() = ''forget_hours''
                       AND owner_user_id = dmbot_current_user())',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_forget_hours() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_hours', true);
        DELETE FROM owner_hours WHERE owner_user_id = dmbot_current_user();
        INSERT INTO session_usage
            (guild_id, campaign_id, session_started_at, owner_user_id, minutes, updated_at)
            SELECT guild_id, campaign_id, session_started_at, 0, minutes, updated_at
              FROM session_usage WHERE owner_user_id = dmbot_current_user()
            ON CONFLICT (guild_id, campaign_id, session_started_at, owner_user_id)
            DO UPDATE SET minutes = session_usage.minutes + EXCLUDED.minutes,
                          updated_at = GREATEST(session_usage.updated_at, EXCLUDED.updated_at);
        DELETE FROM session_usage WHERE owner_user_id = dmbot_current_user();
        -- (an error rolls the setting back with the transaction, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_hours() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_hours() FROM PUBLIC;
"""

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (("dmbot_forget_hours", "dmbot_forget_hours()"),)
