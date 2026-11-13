"""0455_forget_decisions

The second set of columns the audit of #4148 decided (rule 3, the campaign's record): who decided,
set or sent it goes to 0 and the record stays, in `dmbot_forget_decisions()`, called from
`accounts.delete_person` after `dmbot_forget_requests()` (0440), on the pattern of 0432.
"""

from __future__ import annotations

# The campaign's record stays; only "who" goes: the column is set to 0, "someone who has left" (as
# 0432). Audited for keys on 2026-11-12 (#4148): none of these columns is part of a primary key, a
# unique index or a foreign key, so setting several people to 0 can never collide, and a check of
# `> 0` is relaxed to `>= 0` from the table's own text, as in 0432. `recap_jobs.uploaded_by` is the
# DM's uploaded script (the recap job's row stays).
#
# Not here: `lore_uploads.rights_by` (the same person as `user_id`, whose rows 0431 deletes).
#
# Left for the next set (#4148): the tables with a key or a foreign key on the person or on an
# entry (`name_not_same`, `plan_same_as`, `plan_position`, `adventure_reads`, `active_sessions`),
# the arrays (`hidden_by`), `recap_jobs.approved_by` (0 already means "not approved") and
# membership (`campaign_dms`, `campaign_handover_offers`).
#
# (table, the column that holds the person's Discord ID)
ZEROED: tuple[tuple[str, str], ...] = (
    ("house_rule_wins", "decided_by"),
    ("house_rules_file", "set_by"),
    ("pc_icons", "set_by"),
    ("pack_suggestions_sent", "sent_by"),
    ("rule_set_suggestions_sent", "sent_by"),
    ("site_approvals", "approved_by"),
    ("recap_accepted", "accepted_by"),
    ("report_answers", "answered_by"),
    ("plot_fine", "by_user"),
    ("recap_jobs", "uploaded_by"),
)

# Every policy is for the function's owner only and open only while the function runs (its own
# `dmbot.owner_sync` value, as 0425, 0430 and 0431 do). The read policy also shows rows already at
# 0, because Postgres checks the new row of an UPDATE against the SELECT policies too.
_GATE = "dmbot_owner_sync() = ''forget_decisions''"


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
        newrow = " AND ".join(f"{c} IS DISTINCT FROM dmbot_current_user()" for c in columns)
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_decided_read ON {table}\n"
            f"            FOR SELECT TO %I\n"
            f"            USING ({_GATE} AND ({shown}))', current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_decided_update ON {table}\n"
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
    """One UPDATE for each table, setting every column that holds the person at once: a table with
    two such columns (`rule_set_follows`: followed_by and taken_by, often the same person) would
    otherwise be refused by its own policy half way, and a NULL column stays NULL."""
    out = []
    for table in dict.fromkeys(t for t, _ in ZEROED):
        columns = [c for t, c in ZEROED if t == table]
        sets = ", ".join(
            f"{c} = CASE WHEN {c} = dmbot_current_user() THEN 0 ELSE {c} END" for c in columns
        )
        where = " OR ".join(f"{c} = dmbot_current_user()" for c in columns)
        out.append(f"        UPDATE {table} SET {sets} WHERE {where};\n")
    return "".join(out)


SQL = (
    _relax_sql()
    + _policy_sql()
    + r"""
    CREATE FUNCTION dmbot_forget_decisions() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_decisions', true);
"""
    + _body()
    + r"""        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_decisions() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_decisions() FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_decisions", "dmbot_forget_decisions()"),
)
