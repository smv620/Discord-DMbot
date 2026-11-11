"""0431_forget_person

Deleting an account deletes the person's own requests and records (#4141, Supervisor's ruling,
group 1), on the pattern of 0425 (credits) and 0430 (hours): `dmbot_forget_person()`, called from
`accounts.delete_person`.
"""

from __future__ import annotations

# What goes with the account: the website's action queues (a press waiting for the bot, with its
# answer), the person's recorded name in a server's member list, their own notes and reminder
# timers, and what they could do as an editor of a shared ruleset. Each row is the person's own
# request or record, so it is deleted, not kept with the ID set to 0.
# Kept on purpose (not here): `improvement_opt_outs`, because deleting it would quietly turn their
# data back on if their Discord account is ever seen again; the trial and mail records, which are
# ids and times only so a person who signs up again is not given a second free trial or messaged
# twice (0064 and 0065); the campaign's record of what was said or decided (authorship, to follow
# in 0432); the transcripts' speakers and consent (the owner's call).
# `shared_confirmations` is the campaign's record of who confirmed the right to use a text (the IP
# rule: who and when), so it stays with the ID set to 0, "someone who has left".
#
# (table, the column that holds the person's Discord ID)
DELETED: tuple[tuple[str, str], ...] = (
    ("name_actions", "user_id"),
    ("setting_actions", "user_id"),
    ("web_rule_actions", "user_id"),
    ("web_report_requests", "user_id"),
    ("web_restores", "user_id"),
    ("web_line_deletions", "user_id"),
    ("web_line_fixes", "user_id"),
    ("web_recap_style_actions", "user_id"),
    ("web_house_file_actions", "user_id"),
    ("picture_actions", "user_id"),
    ("script_makes", "user_id"),
    ("trait_answers", "user_id"),
    ("upload_undos", "user_id"),
    ("hand_over_answers", "user_id"),
    ("lore_uploads", "user_id"),
    ("web_backups", "user_id"),
    ("recap_requests", "user_id"),
    ("transcript_respells", "user_id"),
    ("web_peeks", "user_id"),
    ("member_directory", "user_id"),
    ("npc_notes", "user_id"),
    ("question_notices", "user_id"),
    ("rule_set_editors", "user_id"),
)
# (table, column) set to 0
ZEROED: tuple[tuple[str, str], ...] = (("shared_confirmations", "user_id"),)

# Every policy is for the function's owner only (`TO current_user`) and open only while the function
# runs (its own `dmbot.owner_sync` value, as 0425 and 0430 do), so no ordinary query of the bot can
# see or change another server's rows through them. A DELETE or an UPDATE needs to read the rows,
# and the new row of an UPDATE must be visible too (Postgres checks it against the SELECT policies),
# hence the read policy that also shows rows already at 0.
_GATE = "dmbot_owner_sync() = ''forget_person''"


def _policies() -> str:
    out = ["    DO $do$\n    BEGIN\n"]
    for table, column in DELETED:
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_read ON {table} FOR SELECT TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_delete ON {table} FOR DELETE TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
        )
    for table, column in ZEROED:
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_read ON {table} FOR SELECT TO %I\n"
            f"            USING ({_GATE} AND ({column} = dmbot_current_user() OR {column} = 0))',\n"
            f"            current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_update ON {table} FOR UPDATE TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())\n"
            f"            WITH CHECK ({_GATE} AND {column} = 0)', current_user);\n"
        )
    out.append("    END\n    $do$;\n")
    return "".join(out)


def _body() -> str:
    lines = [f"        DELETE FROM {t} WHERE {c} = dmbot_current_user();\n" for t, c in DELETED]
    lines += [
        f"        UPDATE {t} SET {c} = 0 WHERE {c} = dmbot_current_user();\n" for t, c in ZEROED
    ]
    return "".join(lines)


SQL = (
    _policies()
    + r"""
    CREATE FUNCTION dmbot_forget_person() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_person', true);
"""
    + _body()
    + r"""        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_person() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_person() FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_person", "dmbot_forget_person()"),
)
