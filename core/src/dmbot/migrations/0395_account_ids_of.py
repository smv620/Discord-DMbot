"""0395_account_ids_of

The Discord IDs that can act as an account (#2533, slice 2a): what the campaign store expands a
campaign's owner and DMs into when it loads them.
"""

from __future__ import annotations

# An account's number is its first Discord ID, so a campaign's `owner_user_id` and `campaign_dms`
# rows keep holding account numbers. The bot asks "is this person a DM" about a Discord ID, so when
# it loads a campaign it asks this once per owner and DM for every Discord ID that acts as that
# account: the number itself (unless the account took that ID off itself, see 0392) and its added
# IDs. An ID the database has never heard of is its own account, so a campaign whose DMs never
# signed in to the website expands to the same IDs it holds today. The function answers only the
# Discord IDs of the account asked about; it is not granted to the website's role (the bot's
# connection is the owner role, which can always run it).
SQL = r"""
    CREATE FUNCTION dmbot_account_logins(acct BIGINT) RETURNS SETOF BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        -- 'move' opens account_discord_ids and account_reserved to this function alone (their
        -- policy, 0392); the previous value is put back before it returns.
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        RETURN QUERY
            SELECT acct WHERE NOT EXISTS (SELECT 1 FROM account_reserved r
                                          WHERE r.discord_id = acct)
            UNION ALL
            SELECT m.discord_id FROM account_discord_ids m WHERE m.account_id = acct;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_account_logins(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_account_logins(BIGINT) FROM PUBLIC;
"""
