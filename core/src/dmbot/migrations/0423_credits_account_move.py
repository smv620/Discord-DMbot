"""0423_credits_account_move

An account move (#1230) carries the AI credits with it (#4094): the old account's month totals and
the ledger's owner follow the new account, as `owner_hours` and `session_usage` do (0068).
"""

from __future__ import annotations

# The move function (0068) changes the account's Discord ID with one UPDATE of `web_users.user_id`.
# Rather than copy that whole function to add two statements, a trigger on that one update does the
# same for the credits: it fires only inside a move (the earlier trigger refuses any other change
# of the ID), so it opens the two tables to itself the way the move door does (`move_all`, for the
# function's owner only, only while the move's setting is on).
# As for the hours: the new account's own credits (a Try It's) are dropped, not added to the paid
# plan's, and the old account's month totals move over. The ledger rows only change owner, so the
# new account's own earlier rows stay in the ledger under it (the ledger can then add up to more
# than the month total, on purpose: nothing recomputes a total from the ledger).
SQL = r"""
    DO $do$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY['owner_credits', 'ai_credit_spend']
        LOOP
            EXECUTE format(
                'CREATE POLICY move_all ON %I FOR ALL TO %I
                    USING (dmbot_owner_sync() = ''move'')
                    WITH CHECK (dmbot_owner_sync() = ''move'')',
                t, current_user);
        END LOOP;
    END
    $do$;
    CREATE FUNCTION dmbot_credits_follow_move() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        DELETE FROM owner_credits WHERE owner_user_id = NEW.user_id;
        -- (the ON CONFLICT is a safety net only: the new account's rows are gone just above)
        INSERT INTO owner_credits (owner_user_id, month_start, credits)
            SELECT NEW.user_id, month_start, credits FROM owner_credits
             WHERE owner_user_id = OLD.user_id
            ON CONFLICT (owner_user_id, month_start)
            DO UPDATE SET credits = owner_credits.credits + EXCLUDED.credits;
        DELETE FROM owner_credits WHERE owner_user_id = OLD.user_id;
        UPDATE ai_credit_spend SET owner_user_id = NEW.user_id WHERE owner_user_id = OLD.user_id;
        RETURN NULL;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_credits_follow_move() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_credits_follow_move() FROM PUBLIC;
    CREATE TRIGGER credits_follow_move AFTER UPDATE OF user_id ON web_users
        FOR EACH ROW WHEN (OLD.user_id IS DISTINCT FROM NEW.user_id)
        EXECUTE FUNCTION dmbot_credits_follow_move();
"""
