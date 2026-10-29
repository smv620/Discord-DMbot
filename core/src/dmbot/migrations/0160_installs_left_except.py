"""0160_installs_left_except

Servers left while offline (#2166).
"""

from __future__ import annotations

INSTALLS_LEFT_EXCEPT = r"""
    -- Servers DMbot was removed from while it was offline (#2166): at start-up the bot lists the
    -- servers it is in, and every install row that still says it is there but is not in the list
    -- gets `left_at`. A plain UPDATE can't do it: the bot's connection sees one server's rows at
    -- a time. This door is the exception; its policy needs the setting the function sets, only
    -- opens the update of a row that has not left, and is TO the role that owns the function
    -- (never the website's, whose own restrictive policy on installs would refuse it anyway).
    -- The function is for the bot: no one else is granted it. It changes no one's installer,
    -- reads nothing back, and returns a count.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY install_left_sweep ON installs FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''installs-left'' AND left_at IS NULL)
                WITH CHECK (dmbot_owner_sync() = ''installs-left'' AND left_at IS NOT NULL)',
            current_user);
        EXECUTE format(
            'CREATE POLICY install_left_sweep_read ON installs FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''installs-left'')',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_installs_left_except(present BIGINT[]) RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        n INTEGER;
    BEGIN
        -- An empty or missing list means the caller could not see its servers, not that it is in
        -- none: marking everything left would be wrong, so nothing is marked.
        IF present IS NULL OR cardinality(present) = 0 THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'installs-left', true);
        UPDATE installs
            SET left_at = floor(extract(epoch FROM clock_timestamp()))::BIGINT
            WHERE left_at IS NULL AND NOT (guild_id = ANY (present));
        GET DIAGNOSTICS n = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN n;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_installs_left_except(BIGINT[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_installs_left_except(BIGINT[]) FROM PUBLIC;
"""

SQL = INSTALLS_LEFT_EXCEPT
