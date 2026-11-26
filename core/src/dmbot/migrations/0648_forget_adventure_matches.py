"""0648_forget_adventure_matches

A person's account deletion drops the Match to files jobs they asked for (#3694, slice G1).
"""

from __future__ import annotations

# `web_adventure_matches` (0647) holds a DM's Match to files jobs and their answers for up to a day:
# ids and names from the DM's book and table. `dmbot_forget_adventure_matches()`, called from
# `accounts.delete_person`, deletes the person's rows (the table has no use for a row with nobody's
# ID in it). The policies are for the function's owner only and open only while it runs (its own
# `dmbot.owner_sync` value, as 0629 and 0497 do).
DELETED: tuple[tuple[str, str], ...] = (("web_adventure_matches", "user_id"),)
_GATE = "dmbot_owner_sync() = ''forget_adventure_matches''"

SQL = (
    r"""
    DO $do$
    BEGIN
"""
    + "".join(
        f"""        EXECUTE format('CREATE POLICY forget_adventure_matches_read ON {t}
            FOR SELECT TO %I
            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);
        EXECUTE format('CREATE POLICY forget_adventure_matches_delete ON {t}
            FOR DELETE TO %I
            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);
"""
        for t, column in DELETED
    )
    + r"""
    END
    $do$;
    CREATE FUNCTION dmbot_forget_adventure_matches() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_adventure_matches', true);
        DELETE FROM web_adventure_matches WHERE user_id = dmbot_current_user();
        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_adventure_matches()'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_adventure_matches() FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_adventure_matches", "dmbot_forget_adventure_matches()"),
)
