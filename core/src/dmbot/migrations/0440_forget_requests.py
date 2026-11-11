"""0440_forget_requests

More of a person's own requests go with their account (#4148, the audit of the columns #4141 left
unreviewed): `dmbot_forget_requests()`, called from `accounts.delete_person` after
`dmbot_forget_authorship()` (0432), on the pattern of 0431.
"""

from __future__ import annotations

# Each is the person's own request or code, so it is deleted, not kept with the ID set to 0:
# - `web_sheet_actions`: the website's character-sheet link/forget queue (a press waiting for the
#   bot, with its answer). Nothing in a key but (guild, campaign, id), so deleting a row is safe.
# - `name_answers`: the website's answers to a name's question, a queue like the others in 0431.
# - `move_codes`: the one-time code that moves the account to another Discord ID. It is keyed by the
#   Discord ID (the primary key), so it can only be deleted, never set to 0.
# Not here: `move_code_asks` goes with the account by its own foreign key (`ON DELETE CASCADE` to
# `web_users`, 0069), so it has no rule of its own beyond the catalog's list.
#
# (table, the column that holds the person's Discord ID)
DELETED: tuple[tuple[str, str], ...] = (
    ("web_sheet_actions", "user_id"),
    ("name_answers", "user_id"),
    ("move_codes", "user_id"),
)

# Every policy is for the function's owner only (`TO current_user`) and open only while the function
# runs (its own `dmbot.owner_sync` value, as 0425, 0430 and 0431 do), so no ordinary query of the
# bot can see or change another server's rows through them.
_GATE = "dmbot_owner_sync() = ''forget_requests''"


def _policies() -> str:
    out = ["    DO $do$\n    BEGIN\n"]
    for table, column in DELETED:
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_requests_read ON {table} FOR SELECT"
            f" TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_requests_delete ON {table} FOR DELETE"
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
    CREATE FUNCTION dmbot_forget_requests() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_requests', true);
"""
    + _body()
    + r"""        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_requests() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_requests() FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_requests", "dmbot_forget_requests()"),
)
