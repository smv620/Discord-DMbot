"""0640_forget_adventure_removals

A person's account deletion drops the file removals they asked for from the website (#3694).
"""

from __future__ import annotations

# `web_adventure_removals` (0639) holds a DM's file removals for up to a day, and for as long as
# they wait or run: no word of the book, only who asked and which file.
# `dmbot_forget_adventure_removals()`, called from `accounts.delete_person`, deletes the person's
# rows (the table has no use for a row with nobody's ID in it). The policies are for the function's
# owner only and open only while it runs (its own `dmbot.owner_sync` value, as 0629 and 0497 do).
DELETED: tuple[tuple[str, str], ...] = (("web_adventure_removals", "user_id"),)
_GATE = "dmbot_owner_sync() = ''forget_adventure_removals''"

SQL = (
    r"""
    DO $do$
    BEGIN
"""
    + "".join(
        f"""        EXECUTE format('CREATE POLICY forget_adventure_removals_read ON {t}
            FOR SELECT TO %I
            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);
        EXECUTE format('CREATE POLICY forget_adventure_removals_delete ON {t}
            FOR DELETE TO %I
            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);
"""
        for t, column in DELETED
    )
    + r"""
    END
    $do$;
    CREATE FUNCTION dmbot_forget_adventure_removals() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_adventure_removals', true);
        DELETE FROM web_adventure_removals WHERE user_id = dmbot_current_user();
        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_adventure_removals()'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_adventure_removals() FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_adventure_removals", "dmbot_forget_adventure_removals()"),
)
