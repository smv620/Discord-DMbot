"""0299_pin_definer_functions

Seven SECURITY DEFINER functions were callable by every role, or had no pinned search path (#3107).
"""

from __future__ import annotations

PIN_DEFINER_FUNCTIONS = r"""
    -- Five helpers from 0294 take a server and a campaign as arguments and check no caller; they
    -- run with their owner's rights, so any role that can connect could call them for any campaign
    -- and read its names, outside the website's own doors. They are called only from inside other
    -- SECURITY DEFINER doors, which run as the owner, so nothing needs a grant: PUBLIC loses its
    -- right to run them. Without a pinned search path a SECURITY DEFINER function can also be
    -- steered by a caller who creates look-alike objects, so each is pinned like every other door
    -- (the same two statements as 0294's block for its other four). `dmbot_web_backup_add` was
    -- rewritten after 0219 pinned it and lost the pin; `dmbot_web_users_id` is a trigger function
    -- (a trigger needs no right to run it when it fires) that PUBLIC could still name.
    -- The website's own grants (the doors it calls) are not touched.
    DO $do$
    DECLARE
        f regprocedure;
    BEGIN
        FOR f IN SELECT p.oid::regprocedure FROM pg_proc p
                 WHERE p.pronamespace = current_schema()::regnamespace
                   AND p.proname IN ('dmbot_my_own_entries', 'dmbot_my_alias_nodes',
                                     'dmbot_my_anchor_name', 'dmbot_my_label_of',
                                     'dmbot_my_alias_text', 'dmbot_web_backup_add',
                                     'dmbot_web_users_id')
        LOOP
            EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',
                           f, current_schema());
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        END LOOP;
    END
    $do$;
"""

SQL = PIN_DEFINER_FUNCTIONS

ISOLATED_TABLES: tuple[str, ...] = ()
