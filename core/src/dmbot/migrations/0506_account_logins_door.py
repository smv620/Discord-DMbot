"""0506_account_logins_door

The website's role can use the account's Discord accounts (#4473, #2533 slice 4, web half). 0392
made `dmbot_account_link` and `dmbot_account_unlink` and the two tables they keep, for the bot's
role: the website's role has no right on the tables and was never given the functions, so the
Account page's list, add and remove were refused ("permission denied for table
account_discord_ids"). Three doors for the website's role, none of them a table right.
"""

from __future__ import annotations

# - `dmbot_account_logins(acct)`: NULL unless `acct` is the signed-in person's own account number
#   (`dmbot_current_user()`), else the Discord accounts that still sign in as a JSON object: the
#   ones added (`id`, `at`) in the order they were added, and whether the account's own first
#   Discord account was taken off. The page never sees the numbers: the web API turns each into a
#   key (`web/discord_accounts.py`).
# - `dmbot_web_account_link(d, now_s)` and `dmbot_web_account_unlink(d, now_s)`: the same two doors
#   0392 made, called for the signed-in person's own account only (`acct` is never an argument
#   here, so one account can never be named for another). Their answer is the door's: 'ok' or the
#   refusal's code. A thin wrapper, so the rules (the lock, the refusals, the history) stay in one
#   place, 0392's.
# Each pins its search path and is closed to every other role; the website's role gets them by name
# (`WEB_ROLE_FUNCTIONS`). No table or row changes.
SQL = r"""
    CREATE FUNCTION dmbot_account_logins(acct BIGINT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF acct IS NULL OR acct IS DISTINCT FROM dmbot_current_user() THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'added', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', m.discord_id,
                                                                   'at', m.added_at)
                                                ORDER BY m.added_at, m.discord_id)
                               FROM account_discord_ids m WHERE m.account_id = acct),
                              '[]'::jsonb),
            'firstOff', EXISTS (SELECT 1 FROM account_reserved r WHERE r.discord_id = acct));
    END
    $fn$;

    CREATE FUNCTION dmbot_web_account_link(d BIGINT, now_s BIGINT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        RETURN dmbot_account_link(dmbot_current_user(), d, now_s);
    END
    $fn$;

    CREATE FUNCTION dmbot_web_account_unlink(d BIGINT, now_s BIGINT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        RETURN dmbot_account_unlink(dmbot_current_user(), d, now_s);
    END
    $fn$;

    DO $do$
    DECLARE
        f regprocedure;
    BEGIN
        FOR f IN SELECT p.oid::regprocedure FROM pg_proc p
                 WHERE p.pronamespace = current_schema()::regnamespace
                   AND p.proname IN ('dmbot_account_logins', 'dmbot_web_account_link',
                                     'dmbot_web_account_unlink')
        LOOP
            EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',
                           f, current_schema());
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        END LOOP;
    END
    $do$;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_account_logins", "dmbot_account_logins(BIGINT)"),
    ("dmbot_web_account_link", "dmbot_web_account_link(BIGINT, BIGINT)"),
    ("dmbot_web_account_unlink", "dmbot_web_account_unlink(BIGINT, BIGINT)"),
)
