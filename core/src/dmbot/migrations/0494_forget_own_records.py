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
# Not here, decided on #4148: `map_notes` are a person's own words put into pictures' descriptions
# and go when the person deletes their own words (`TranscriptStore.delete_person`, not on account
# deletion), the owner's call with the transcripts (rule 4); `account_moves` and `access_log` are kept by design (see the
# catalog's reasons).
#
# (table, the column that holds the person's Discord ID)
DELETED: tuple[tuple[str, str], ...] = (
    ("plot_chat", "user_id"),
    ("web_delete_notices", "by_user_id"),
)

# Both are forced-RLS tables, so each needs its policies (no table here is unscoped).
NO_RLS: frozenset[str] = frozenset()

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


def _body() -> str:
    return "".join(
        f"        DELETE FROM {t} WHERE {c} = dmbot_current_user();\n" for t, c in DELETED
    )


SQL = (
    _policies()
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
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_own_records", "dmbot_forget_own_records()"),
)
