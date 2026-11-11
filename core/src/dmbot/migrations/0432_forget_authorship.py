"""0432_forget_authorship

Deleting an account takes the person's Discord ID out of what they wrote or decided for a campaign,
and keeps the record (#4141, Supervisor's ruling, rule 3): `dmbot_forget_authorship()`, called from
`accounts.delete_person` after `dmbot_forget_person()` (0431).
"""

from __future__ import annotations

# The campaign's record of what was said or decided stays; only "who" goes: the column is set to 0,
# "someone who has left" (the same 0 as `shared_confirmations` in 0431). Shared rulesets keep the
# set, published by nobody. A column that said "greater than 0" is relaxed to "0 or more", worked
# out from the table's own check, because a plain `UPDATE ... = 0` would be refused. None of these
# columns is part of a key (audited), so setting several people to 0 can never collide.
#
# Left out on purpose, with the reasons in `docs/PLAN.md` (rule 4 and the table's other rows): who
# spoke in a transcript and who consented (the owner's call), and columns not yet audited for a key
# that would collide at 0 (they are listed as UNREVIEWED in `tests/test_account_deletion.py`).
#
# (table, the column that holds the person's Discord ID)
ZEROED: tuple[tuple[str, str], ...] = (
    ("house_rules", "created_by"),
    ("rule_sets", "created_by"),
    ("rule_set_versions", "created_by"),
    ("rule_set_follows", "followed_by"),
    ("rule_set_follows", "taken_by"),
    ("rule_set_editors", "added_by"),
    ("homebrew_entries", "created_by"),
    ("adventure_documents", "added_by"),
    ("adventure_chunks", "created_by"),
    ("recap_style_examples", "created_by"),
    ("recap_style_notes", "created_by"),
    ("recap_must_haves", "created_by"),
    ("recap_jobs", "dm_user_id"),
    ("recap_holds", "dm_user_id"),
    ("speaker_removals", "requested_by"),
    ("session_imports", "imported_by"),
    ("recap_upload_rights", "user_id"),
    ("transcript_suggestions", "decided_by"),
    ("waiting_name_questions", "decided_by"),
)

# Every policy is for the function's owner only and open only while the function runs (its own
# `dmbot.owner_sync` value, as 0425, 0430 and 0431 do). The read policy also shows rows already at
# 0, because Postgres checks the new row of an UPDATE against the SELECT policies too.
_GATE = "dmbot_owner_sync() = ''forget_authorship''"


def _policy_sql() -> str:
    out = ["    DO $do$\n    BEGIN\n"]
    seen: set[str] = set()
    for table, _column in ZEROED:
        # a table with two such columns (rule_set_follows) gets one pair of policies, over both
        if table in seen:
            continue
        seen.add(table)
        columns = [c for t, c in ZEROED if t == table]
        mine = " OR ".join(f"{c} = dmbot_current_user()" for c in columns)
        shown = " OR ".join(f"{c} = dmbot_current_user() OR {c} = 0" for c in columns)
        newrow = " AND ".join(f"{c} <> dmbot_current_user()" for c in columns)
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_author_read ON {table}\n"
            f"            FOR SELECT TO %I\n"
            f"            USING ({_GATE} AND ({shown}))', current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_author_update ON {table}\n"
            f"            FOR UPDATE TO %I\n"
            f"            USING ({_GATE} AND ({mine}))\n"
            f"            WITH CHECK ({_GATE} AND {newrow})', current_user);\n"
        )
    out.append("    END\n    $do$;\n")
    return "".join(out)


def _relax_sql() -> str:
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
    return "".join(
        f"        UPDATE {t} SET {c} = 0 WHERE {c} = dmbot_current_user();\\n".replace("\\n", "\n")
        for t, c in ZEROED
    )


SQL = (
    _relax_sql()
    + _policy_sql()
    + r"""
    CREATE FUNCTION dmbot_forget_authorship() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_authorship', true);
"""
    + _body()
    + r"""        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_authorship() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_authorship() FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_authorship", "dmbot_forget_authorship()"),
)
