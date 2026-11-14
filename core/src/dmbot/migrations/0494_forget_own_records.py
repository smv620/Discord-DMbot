"""0494_forget_own_records

The person's own records go with their account (#4148, the audit of the columns #4141 left
unreviewed): `dmbot_forget_own_records()`, called from `accounts.delete_person` after
`dmbot_forget_decisions_2()` (0493), on the pattern of 0440.
"""

from __future__ import annotations

# Each is the person's own, so it is deleted, not kept with the ID set to 0:
# - `plot_chat`: the DM's talk with PlotBot about one issue (their messages and the bot's answers to
#   them, all under their `user_id`). It is the DM's own, read by the DM who wrote it and no one
#   else, never in a transcript, a backup or a public link (its schema comment), and nothing in the
#   story until the DM pressed Save. Nothing references it.
# - `web_delete_notices`: the note the website's delete door leaves for the bot (the campaign's
#   name, its channel IDs and the other game masters), taken by the bot within seconds and swept
#   after a week. `by_user_id` is who deleted it. Nothing references it; the other game masters'
#   IDs in `other_dms` are a short-lived queue entry that goes with the row or the sweep, and the
#   campaign it names no longer exists. A notice another game master wrote can still list the
#   person's ID in `other_dms` until the bot takes it (seconds) or the weekly sweep removes it; and
#   a person who deletes a campaign on the website and then their account before the bot takes the
#   note leaves the other game masters untold and the channels untidied (a window of seconds).
# A row can be deleted while the bot is working on it; the worker's later `UPDATE ... WHERE id` then
# matches nothing, which it already treats as "taken back" (as for the queues in 0431 and 0440).
#
# Also here, as Supervisor ruled on #4411:
# - `access_log` (the admin's record of who was given free access): kept, the Discord ID set to 0
#   (rule 3), its `> 0` check relaxed to `>= 0`; it has forced row-level security, so it has the
#   function's own policies.
# - `account_moves` (the moved marker, routing a moved ID and pointing the payment company's records
#   at the new one): the person's rows (as the old or the new ID) are deleted once the payment
#   update is done (`stripe_done_at`), or has no ids to update, or gave up (`GIVE_UP` tries; the
#   retries back off to an hour, so that is more than half a day). A row whose update is still to do
#   is marked `forgotten_at` instead, and `dmbot_move_synced` / `dmbot_move_failed` delete it when
#   the update is done or gives up. The trial block does not need this row: `dmbot_move_account`
#   writes `try_it_used` for the old and the new ID with their keyed hashes (0460), which stay.
#
# Not here, decided on #4148: `map_notes` are a person's own words put into pictures' descriptions
# and go when the person deletes their own words (`TranscriptStore.delete_person`, not on account
# deletion), the owner's call with the transcripts (rule 4).
#
# (table, the column that holds the person's Discord ID)
DELETED: tuple[tuple[str, str], ...] = (
    ("plot_chat", "user_id"),
    ("web_delete_notices", "by_user_id"),
)

# Both are forced-RLS tables, so each needs its policies (no table here is unscoped).
NO_RLS: frozenset[str] = frozenset()

# (table, column): the Discord ID goes to 0 and the record stays.
ZEROED: tuple[tuple[str, str], ...] = (("access_log", "discord_user_id"),)
# The moved marker's two columns that hold a person (see the notes above for what happens to a row).
MOVES: tuple[tuple[str, str], ...] = (
    ("account_moves", "old_user_id"),
    ("account_moves", "new_user_id"),
)
GIVE_UP = 20  # payment-update tries after which a forgotten move's row is let go

# Every policy is for the function's owner only (`TO current_user`) and open only while the function
# runs (its own `dmbot.owner_sync` value, as 0425, 0430 and 0431 do), so no ordinary query of the
# bot can see or change another server's rows through them.
_GATE = "dmbot_owner_sync() = ''forget_own_records''"


def _policies() -> str:
    out = ["    DO $do$\n    BEGIN\n"]
    for table, column in DELETED:
        if table in NO_RLS:
            continue
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_own_records_read ON {table} FOR SELECT"
            f" TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_own_records_delete ON {table} FOR DELETE"
            f" TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
        )
    out.append("    END\n    $do$;\n")
    return "".join(out)


def _zero_policies() -> str:
    out = ["    DO $do$\n    BEGIN\n"]
    for table, column in ZEROED:
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_own_records_zero_read ON {table}\n"
            f"            FOR SELECT TO %I\n"
            f"            USING ({_GATE} AND ({column} = dmbot_current_user() OR {column} = 0))',\n"
            f"            current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_own_records_zero_update ON {table}\n"
            f"            FOR UPDATE TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())\n"
            f"            WITH CHECK ({_GATE}\n"
            f"                AND {column} IS DISTINCT FROM dmbot_current_user())',\n"
            f"            current_user);\n"
        )
    out.append("    END\n    $do$;\n")
    return "".join(out)


def _relax() -> str:
    """`discord_user_id > 0` becomes `>= 0`, from the table's own text, as 0432 and 0455 do."""
    values = ", ".join(f"('{t}', '{c}')" for t, c in ZEROED)
    return f"""
    DO $do$
    DECLARE
        v RECORD;
        c RECORD;
    BEGIN
        FOR v IN SELECT * FROM (VALUES {values}) AS x(tbl, col) LOOP
            FOR c IN
                SELECT con.conname AS name, pg_get_constraintdef(con.oid) AS def
                  FROM pg_constraint con
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
                 WHERE con.conrelid = v.tbl::regclass AND con.contype = 'c'
                   AND a.attname = v.col AND array_length(con.conkey, 1) = 1
                   AND pg_get_constraintdef(con.oid) ~ '> 0'
            LOOP
                EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', v.tbl, c.name);
                EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I %s', v.tbl, c.name,
                               replace(c.def, '> 0', '>= 0'));
            END LOOP;
        END LOOP;
    END
    $do$;
"""


def _body() -> str:
    deleted = "".join(
        f"        DELETE FROM {t} WHERE {c} = dmbot_current_user();\n" for t, c in DELETED
    )
    zeroed = "".join(
        f"        UPDATE {t} SET {c} = 0 WHERE {c} = dmbot_current_user();\n" for t, c in ZEROED
    )
    moves = f"""        -- (the moved marker: done rows go now, the rest go with their update)
        DELETE FROM account_moves
            WHERE (old_user_id = dmbot_current_user() OR new_user_id = dmbot_current_user())
              AND (stripe_done_at IS NOT NULL
                   OR (customer_id IS NULL AND subscription_id IS NULL)
                   OR stripe_attempts >= {GIVE_UP});
        UPDATE account_moves SET forgotten_at = extract(epoch FROM now())::BIGINT
            WHERE (old_user_id = dmbot_current_user() OR new_user_id = dmbot_current_user())
              AND forgotten_at IS NULL;
"""
    return deleted + zeroed + moves


SQL = (
    r"""
    -- A move whose person has deleted their account: its row is let go when its payment update is
    -- done or gives up (`dmbot_move_synced` and `dmbot_move_failed` below).
    ALTER TABLE account_moves ADD COLUMN forgotten_at BIGINT;
"""
    + _relax()
    + _zero_policies()
    + _policies()
    + r"""
    CREATE FUNCTION dmbot_forget_own_records() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_own_records', true);
"""
    + _body()
    + r"""        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_own_records() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_own_records() FROM PUBLIC;
"""
    + f"""
    CREATE OR REPLACE FUNCTION dmbot_move_failed(who BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE account_moves SET stripe_attempts = stripe_attempts + 1,
                                 stripe_last_try_at = extract(epoch FROM now())::BIGINT
            WHERE old_user_id = who AND stripe_done_at IS NULL;
        DELETE FROM account_moves
            WHERE old_user_id = who AND forgotten_at IS NOT NULL AND stripe_attempts >= {GIVE_UP};
    $fn$;
    CREATE OR REPLACE FUNCTION dmbot_move_synced(who BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE account_moves SET stripe_done_at = extract(epoch FROM now())::BIGINT
            WHERE old_user_id = who AND stripe_done_at IS NULL;
        DELETE FROM account_moves WHERE old_user_id = who AND forgotten_at IS NOT NULL;
    $fn$;
    """
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_failed(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_synced(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_move_failed(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_move_synced(BIGINT) FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_own_records", "dmbot_forget_own_records()"),
)
