"""0175_recap_address_co_dm

A co-DM's recap email address (#2360).
"""

from __future__ import annotations

RECAP_ADDRESS_CO_DM = """
    -- A co-DM with no recap job of their own gets the recap email too (#2360): the address is
    -- answered for any CURRENT DM of a campaign that has a recap job or a held recap (a recap
    -- waiting for name answers, whose waiting email also goes to the DMs) in this server. Being the
    -- DM a job was made for is no longer enough: someone who has since stopped being a DM of the
    -- campaign gets nothing. Still one person's own address, still only while the recap worker's
    -- door is open. The rows read are this server's (forced row-level security), so no other
    -- server's DMs are seen.
    CREATE OR REPLACE FUNCTION dmbot_recap_address(who BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found TEXT;
        before TEXT := COALESCE(current_setting('dmbot.recap_mail_lookup', true), '');
    BEGIN
        IF dmbot_recap_mail() IS DISTINCT FROM 'mail' THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM campaign_dms d
            WHERE d.user_id = who
              AND (EXISTS (SELECT 1 FROM recap_jobs j
                           WHERE j.guild_id = d.guild_id AND j.campaign_id = d.campaign_id)
                   OR EXISTS (SELECT 1 FROM recap_holds h
                              WHERE h.guild_id = d.guild_id AND h.campaign_id = d.campaign_id))
        ) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_mail_lookup', 'on', true);
        SELECT COALESCE(payment_email, email) INTO found FROM web_users WHERE user_id = who;
        PERFORM set_config('dmbot.recap_mail_lookup', before, true);
        RETURN found;
    END
    $fn$;
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_address(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_address(BIGINT) FROM PUBLIC;
"""

SQL = RECAP_ADDRESS_CO_DM
