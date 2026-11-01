"""0016_web_role_limits."""

from __future__ import annotations

WEB_ROLE_LIMITS = """
    -- The servers in the signed-in session's own Discord list (#498): the session on this
    -- request (dmbot.session), belonging to the signed-in person (dmbot.user_id), not
    -- expired. Empty otherwise. Policies call it as (SELECT ...), so it runs once per
    -- statement, not per row. The policies themselves are made with the role's rights
    -- (WEB_ROLE_POLICIES): a policy for a role can only be made once the role exists.
    CREATE FUNCTION dmbot_web_guilds() RETURNS BIGINT[]
        LANGUAGE sql STABLE
        AS $fn$
            SELECT coalesce(array_agg((e->>'id')::BIGINT), '{}')
            FROM web_sessions s, jsonb_array_elements(s.guilds) AS e
            WHERE s.id_hash = dmbot_current_session()
              AND s.user_id = dmbot_current_user()
              AND s.expires_at > dmbot_now()
        $fn$;

    -- The same, only the servers the person could add bots to when they signed in.
    CREATE FUNCTION dmbot_web_managed_guilds() RETURNS BIGINT[]
        LANGUAGE sql STABLE
        AS $fn$
            SELECT coalesce(array_agg((e->>'id')::BIGINT), '{}')
            FROM web_sessions s, jsonb_array_elements(s.guilds) AS e
            WHERE s.id_hash = dmbot_current_session()
              AND s.user_id = dmbot_current_user()
              AND s.expires_at > dmbot_now()
              AND (e->>'manage')::BOOLEAN
        $fn$;
    """

SQL = WEB_ROLE_LIMITS
