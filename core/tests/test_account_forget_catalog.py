"""Every table that holds a person's Discord ID has a rule for account deletion (#4141, Supervisor's
ruling; docs/PLAN.md, the table under "Deleting an account"). A table added with such a column
fails here until it is put on a rule, so the next person to add one has to decide. Needs Postgres.

The rules: deleted with the account (0431), set to 0 and kept (0431, 0432), handled by another
forget function or a cascade, kept by design, a campaign's transcripts, speakers and consent (the
owner's call), and UNREVIEWED: not yet decided, listed for Supervisor on #4141.
"""

from __future__ import annotations

from importlib import import_module

from tests.pg import DatabaseTest

# What counts as a column that holds a person's Discord ID.
PERSON_COLUMN = r"(user|_by$|^by_|speaker|owner|member|player|author|discord)"

Rules = dict[str, tuple[str, ...] | None]

# Each rule maps a table to None (every person column of the table) or to the columns it covers.
# Their own forget function (credits, hours, wizard, mail, history, grants) or a cascade:
ELSEWHERE: Rules = {
    t: None
    for t in (
        "access_grants", "account_discord_ids", "account_id_history", "account_link_history",
        "account_reserved", "ai_credit_spend", "owner_credits", "owner_hours",
        "session_usage", "owner_campaigns", "campaigns", "email_changes", "email_sign_in_links",
        "sent_emails", "entitlements", "web_sessions",
        "web_users", "support_tickets", "wizard_documents", "wizard_drafts", "wizard_finishes",
        "dm_players",  # (0450: its own forget function, dmbot_forget_dm_players, #4226)
        "installs", "move_code_asks",  # (move_code_asks: ON DELETE CASCADE from web_users, 0069)
    )
}  # fmt: skip
BY_DESIGN: Rules = {  # kept on purpose: protects the person, or stops a second free trial (#4145)
    "improvement_opt_outs": None,
    "trial_extensions": None,
    "try_it_used": None,
    "email_unsubscribed": None,  # the unsubscribe list is kept so nobody is emailed again
    "payment_events": None,  # the payment record is kept (the delete_person docstring)
}
# The campaign's transcripts, speakers and consent: the owner's call (rule 4):
TRANSCRIPTS: Rules = {
    t: None
    for t in (
        "campaign_speakers", "consent", "consent_refusals", "import_consents", "import_plays",
        "import_speakers", "graph_reviews", "memory_claims", "memory_deed_proposals",
        "memory_heard", "memory_met", "memory_private_scenes", "memory_reveal_proposals",
        "memory_scenes", "memory_who_answers", "rel_ties", "said_as_hears", "said_as_listens",
        "speaker_erasures", "test_voice_consent", "theatre_lines", "transcript_erasures",
        "transcript_lines", "transcript_respells", "transcript_sessions", "character_sheets",
        "fightbot_players", "campaign_channel_people",
    )
}  # fmt: skip
TRANSCRIPTS.update(
    {
        "memory_entities": ("played_by",),
        "speaker_removals": ("speaker",),
        "transcript_suggestions": ("user_id",),
        "waiting_name_questions": ("speaker_id",),
        "web_line_fixes": ("speaker",),
    }
)
NOT_A_PERSON: Rules = {  # matched by name only
    "game_clocks": None,  # channel and message ids
    "campaigns": ("owner_left_at", "owner_missing_since"),  # times
}
UNREVIEWED: Rules = {  # not yet decided: the list for Supervisor on #4141 (columns not yet audited)
    "access_log": None, "account_moves": None, "active_sessions": None, "adventure_reads": None,
    "campaign_dms": None, "campaign_handover_offers": None,
    "map_notes": None, "memory_beliefs": None, "memory_knowers": None,
    "name_not_same": None, "pack_suggestions": None,
    "plan_position": None, "plan_same_as": None,
    "plot_chat": None, "plot_issues": None,
    "recap_jobs": ("approved_by", "speakers"),
    "rule_set_suggestions": None,
    "web_delete_notices": None,
}  # fmt: skip


def _covers(rules: Rules, table: str, column: str) -> bool:
    if table not in rules:
        return False
    only = rules[table]
    return only is None or column in only


class EveryPersonColumnHasARule(DatabaseTest):
    async def test_no_table_with_a_person_column_is_without_a_rule(self) -> None:
        part_a = import_module("dmbot.migrations.0431_forget_person")
        part_b = import_module("dmbot.migrations.0432_forget_authorship")
        part_c = import_module("dmbot.migrations.0440_forget_requests")
        part_d = import_module("dmbot.migrations.0455_forget_decisions")
        done = {*part_a.DELETED, *part_a.ZEROED, *part_b.ZEROED, *part_c.DELETED, *part_d.ZEROED}
        deleted_tables = {t for t, _ in (*part_a.DELETED, *part_c.DELETED)}
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND data_type IN ('bigint', 'ARRAY')"
                " AND column_name ~ %s ORDER BY 1, 2",
                (PERSON_COLUMN,),
            )
            found = [(r["table_name"], r["column_name"]) for r in await cur.fetchall()]
        missing = []
        for table, column in found:
            if (table, column) in done or table in deleted_tables:
                continue  # (a table whose rows are all deleted needs no column rule)
            if any(
                _covers(rules, table, column)
                for rules in (ELSEWHERE, BY_DESIGN, TRANSCRIPTS, NOT_A_PERSON, UNREVIEWED)
            ):
                continue
            missing.append(f"{table}.{column}")
        self.assertEqual(
            missing,
            [],
            "a column that holds a person's Discord ID has no rule for account deletion: add it "
            "to DELETED or ZEROED in a forget migration, or to a rule here with its reason "
            "(docs/PLAN.md, 'Deleting an account')",
        )

    async def test_nothing_is_listed_twice_or_for_a_table_that_is_gone(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema ="
                " current_schema()"
            )
            tables = {r["table_name"] for r in await cur.fetchall()}
        every = (ELSEWHERE, BY_DESIGN, TRANSCRIPTS, NOT_A_PERSON, UNREVIEWED)
        for rules in every:
            self.assertLessEqual(set(rules), tables, set(rules) - tables)
        for table in tables:
            full = [r for r in every if table in r and r[table] is None]
            self.assertLessEqual(len(full), 1, table)  # (one rule for the whole table)
