"""0392_account_discord_ids

One DMbot account can hold several Discord accounts (#2533, slice 1: the data and the one resolver,
no behaviour change yet).
"""

from __future__ import annotations

# The account's number stays its first Discord ID (`web_users.user_id`), so no owner, DM, plan or
# hours column is rewritten. A second Discord ID is a row here that points at the account.
#
# Three tables:
#   account_discord_ids   the extra Discord IDs of an account (never the account's own number)
#   account_reserved      a first Discord ID the account took off itself: it stays the account's
#                         number for good, but it is nobody's login (Supervisor, #2533), so it can
#                         never sign in fresh and make a second account with the same number
#   account_link_history  every add and remove, never changed. It is its own table because
#                         account_id_history (0068) is one row per move and its columns (old, new)
#                         do not fit "added" or "removed"; both are read together for the export.
#
# Row-level security: an account reads its own rows. Writing is only through the two doors below
# and the sign-in trigger, which open the tables to themselves with the same `dmbot.owner_sync`
# setting the move doors use ('move'), set and put back inside each function.
SQL = r"""
    CREATE TABLE account_discord_ids (
        discord_id BIGINT PRIMARY KEY CHECK (discord_id > 0),
        account_id BIGINT NOT NULL REFERENCES web_users (user_id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        added_at   BIGINT NOT NULL,
        CHECK (discord_id <> account_id)
    );
    CREATE INDEX account_discord_ids_by_account ON account_discord_ids (account_id);

    CREATE TABLE account_reserved (
        discord_id BIGINT PRIMARY KEY CHECK (discord_id > 0),
        account_id BIGINT NOT NULL REFERENCES web_users (user_id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        removed_at BIGINT NOT NULL,
        CHECK (discord_id = account_id)
    );

    CREATE TABLE account_link_history (
        id         BIGSERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL REFERENCES web_users (user_id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        discord_id BIGINT NOT NULL CHECK (discord_id > 0),
        action     TEXT NOT NULL CHECK (action IN ('added', 'removed')),
        at         BIGINT NOT NULL
    );
    CREATE INDEX account_link_history_by_account ON account_link_history (account_id, id);

    DO $do$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY['account_discord_ids', 'account_reserved',
                                 'account_link_history']
        LOOP
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
            EXECUTE format(
                'CREATE POLICY own_read ON %I FOR SELECT'
                ' USING (account_id = dmbot_current_user())', t);
            EXECUTE format(
                'CREATE POLICY door_all ON %I FOR ALL TO %I'
                ' USING (dmbot_owner_sync() = ''move'')'
                ' WITH CHECK (dmbot_owner_sync() = ''move'')', t, current_user);
        END LOOP;
    END
    $do$;

    -- Which account a Discord ID belongs to: its own number if it is an account's first ID that is
    -- still in use, the account it was added to, or NULL (a removed first ID, or nobody). This is
    -- the only function that maps a Discord ID to an account. It answers an account number or
    -- nothing, never a list of someone else's IDs.
    CREATE FUNCTION dmbot_account_of(d BIGINT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        found BIGINT;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        IF EXISTS (SELECT 1 FROM account_reserved r WHERE r.discord_id = d) THEN
            found := NULL;
        ELSE
            SELECT m.account_id INTO found FROM account_discord_ids m WHERE m.discord_id = d;
            IF found IS NULL THEN
                SELECT u.user_id INTO found FROM web_users u WHERE u.user_id = d;
            END IF;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN found;
    END
    $fn$;

    -- Add a Discord ID to the signed-in account. 'ok', or why not: 'not_yours' (the caller is not
    -- that account), 'taken' (it is an account's number, a removed one, or already someone's),
    -- 'has_account' (it already has its own DMbot account; slice 3 decides what an empty one
    -- becomes), 'has_campaigns' (it owns or is a DM of a campaign) or 'has_plan' (it has its own
    -- plan or hours). Hand the campaigns over or end the plan first.
    CREATE FUNCTION dmbot_account_link(acct BIGINT, d BIGINT, now_s BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        res TEXT;
    BEGIN
        IF acct IS NULL OR d IS NULL OR d <= 0 OR acct IS DISTINCT FROM dmbot_current_user() THEN
            RETURN 'not_yours';
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        -- The same lock the sign-in trigger takes, so a sign-in and an add of one ID at once
        -- cannot both succeed.
        PERFORM pg_advisory_xact_lock(hashtextextended('account_id:' || d::TEXT, 0));
        res := CASE
            WHEN NOT EXISTS (SELECT 1 FROM web_users u WHERE u.user_id = acct) THEN 'not_yours'
            WHEN d = acct
              OR EXISTS (SELECT 1 FROM account_reserved r WHERE r.discord_id = d)
              OR EXISTS (SELECT 1 FROM account_discord_ids m WHERE m.discord_id = d)
              OR EXISTS (SELECT 1 FROM account_moves a WHERE a.old_user_id = d) THEN 'taken'
            WHEN EXISTS (SELECT 1 FROM campaigns c WHERE c.owner_user_id = d)
              OR EXISTS (SELECT 1 FROM campaign_dms x WHERE x.user_id = d) THEN 'has_campaigns'
            WHEN EXISTS (SELECT 1 FROM entitlements e WHERE e.user_id = d)
              OR EXISTS (SELECT 1 FROM owner_hours h WHERE h.owner_user_id = d) THEN 'has_plan'
            WHEN EXISTS (SELECT 1 FROM web_users u WHERE u.user_id = d) THEN 'has_account'
            ELSE 'ok'
        END;
        IF res = 'ok' THEN
            INSERT INTO account_discord_ids (discord_id, account_id, added_at)
                VALUES (d, acct, now_s);
            INSERT INTO account_link_history (account_id, discord_id, action, at)
                VALUES (acct, d, 'added', now_s);
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN res;
    END
    $fn$;

    -- Take a Discord ID off the signed-in account, never the last one that still signs in.
    -- 'ok', 'not_yours', 'not_linked' (it is not one of this account's) or 'last'. Taking off the
    -- account's own first ID keeps the account's number but reserves the ID for good; an added ID
    -- is simply deleted (it may then be added again, to any account). The account's campaigns stay
    -- with the account either way.
    CREATE FUNCTION dmbot_account_unlink(acct BIGINT, d BIGINT, now_s BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        res TEXT;
        extra INT;
        first_in_use BOOLEAN;
    BEGIN
        IF acct IS NULL OR d IS NULL OR acct IS DISTINCT FROM dmbot_current_user() THEN
            RETURN 'not_yours';
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        PERFORM pg_advisory_xact_lock(hashtextextended('account_id:' || acct::TEXT, 0));
        SELECT count(*) INTO extra FROM account_discord_ids m WHERE m.account_id = acct;
        first_in_use := NOT EXISTS (SELECT 1 FROM account_reserved r WHERE r.discord_id = acct);
        IF NOT EXISTS (SELECT 1 FROM web_users u WHERE u.user_id = acct) THEN
            res := 'not_yours';
        ELSIF d = acct THEN
            IF NOT first_in_use THEN
                res := 'not_linked';
            ELSIF extra = 0 THEN
                res := 'last';
            ELSE
                INSERT INTO account_reserved (discord_id, account_id, removed_at)
                    VALUES (acct, acct, now_s);
                res := 'ok';
            END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM account_discord_ids m
                          WHERE m.discord_id = d AND m.account_id = acct) THEN
            res := 'not_linked';
        ELSIF NOT first_in_use AND extra = 1 THEN
            res := 'last';
        ELSE
            DELETE FROM account_discord_ids m WHERE m.discord_id = d AND m.account_id = acct;
            res := 'ok';
        END IF;
        IF res = 'ok' THEN
            INSERT INTO account_link_history (account_id, discord_id, action, at)
                VALUES (acct, d, 'removed', now_s);
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN res;
    END
    $fn$;

    -- A Discord ID that is another account's added ID, or a first ID the account took off itself,
    -- cannot make an account of its own (the website shows the hint's words). Replaces 0068's
    -- version with the same body plus these two checks, so it is pinned again below (a replaced
    -- function loses its pin, see 0299).
    CREATE OR REPLACE FUNCTION dmbot_web_users_id() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        refusal TEXT;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF EXISTS (SELECT 1 FROM account_moves WHERE old_user_id = NEW.user_id) THEN
                RAISE EXCEPTION 'This account moved'
                    USING ERRCODE = 'P0001', HINT = 'account_moved';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('account_id:' || NEW.user_id::TEXT, 0));
            PERFORM set_config('dmbot.owner_sync', 'move', true);
            SELECT CASE
                WHEN EXISTS (SELECT 1 FROM account_reserved r WHERE r.discord_id = NEW.user_id)
                    THEN 'account_removed'
                WHEN EXISTS (SELECT 1 FROM account_discord_ids m WHERE m.discord_id = NEW.user_id)
                    THEN 'account_linked'
            END INTO refusal;
            PERFORM set_config('dmbot.owner_sync', before, true);
            IF refusal IS NOT NULL THEN
                RAISE EXCEPTION 'This Discord account belongs to a DMbot account'
                    USING ERRCODE = 'P0001', HINT = refusal;
            END IF;
            NEW.dmbot_id := dmbot_base36(NEW.user_id);
        ELSIF NEW.dmbot_id IS DISTINCT FROM OLD.dmbot_id THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        ELSIF NEW.user_id <> OLD.user_id AND dmbot_owner_sync() <> 'move' THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        END IF;
        RETURN NEW;
    END
    $fn$;

    DO $do$
    DECLARE
        f regprocedure;
    BEGIN
        FOR f IN SELECT p.oid::regprocedure FROM pg_proc p
                 WHERE p.pronamespace = current_schema()::regnamespace
                   AND p.proname IN ('dmbot_account_of', 'dmbot_account_link',
                                     'dmbot_account_unlink', 'dmbot_web_users_id')
        LOOP
            EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',
                           f, current_schema());
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        END LOOP;
    END
    $do$;
"""

USER_ISOLATED_TABLES: tuple[str, ...] = (
    "account_discord_ids",
    "account_reserved",
    "account_link_history",
)

# No door is given to the website's role yet: the add and remove routes are slice 3 (#2533), which
# lists `dmbot_account_link` and `dmbot_account_unlink` here when it builds them.
