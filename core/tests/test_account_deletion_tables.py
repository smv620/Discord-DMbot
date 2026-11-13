"""Every table account deletion changes is tested with real rows (#4162): one row for
Alice and one for Bob in each, then the deletion. Alice's rows go from the DELETED tables and her
person column goes to 0 in the ZEROED ones; Bob's rows stay untouched. The lists come from the
migrations themselves, so a table added to a forget function fails the completeness test until it
has a row here. Needs Postgres."""

from __future__ import annotations

from importlib import import_module

from dmbot import entitlements
from dmbot.campaigns import CampaignStore
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB = 7001, 7002
GUILD_A = 111
NOW = 1_800_000_000

FORGET_PERSON = import_module("dmbot.migrations.0431_forget_person")
FORGET_AUTHORSHIP = import_module("dmbot.migrations.0432_forget_authorship")
FORGET_REQUESTS = import_module("dmbot.migrations.0440_forget_requests")
FORGET_DECISIONS = import_module("dmbot.migrations.0455_forget_decisions")
FORGET_LOOKUPS = import_module("dmbot.migrations.0447_member_lookups")
DELETED: tuple[tuple[str, str], ...] = (
    FORGET_PERSON.DELETED + FORGET_REQUESTS.DELETED + FORGET_LOOKUPS.DELETED
)
ZEROED: tuple[tuple[str, str], ...] = (
    FORGET_PERSON.ZEROED + FORGET_AUTHORSHIP.ZEROED + FORGET_DECISIONS.ZEROED
)

# Written out on purpose: if a column is dropped from a forget function, this test fails too, so
# the drop shows up here as well as in the migration's review.
EXPECTED_DELETED: frozenset[tuple[str, str]] = frozenset(
    (
        ("hand_over_answers", "user_id"),
        ("member_lookups", "user_id"),
        ("move_codes", "user_id"),
        ("name_answers", "user_id"),
        ("web_sheet_actions", "user_id"),
        ("lore_uploads", "user_id"),
        ("member_directory", "user_id"),
        ("name_actions", "user_id"),
        ("npc_notes", "user_id"),
        ("picture_actions", "user_id"),
        ("question_notices", "user_id"),
        ("recap_requests", "user_id"),
        ("rule_set_editors", "user_id"),
        ("script_makes", "user_id"),
        ("setting_actions", "user_id"),
        ("trait_answers", "user_id"),
        ("upload_undos", "user_id"),
        ("web_backups", "user_id"),
        ("web_house_file_actions", "user_id"),
        ("web_line_deletions", "user_id"),
        ("web_line_fixes", "user_id"),
        ("web_peeks", "user_id"),
        ("web_recap_style_actions", "user_id"),
        ("web_report_requests", "user_id"),
        ("web_restores", "user_id"),
        ("web_rule_actions", "user_id"),
    )
)
EXPECTED_ZEROED: frozenset[tuple[str, str]] = frozenset(
    (
        ("adventure_chunks", "created_by"),
        ("adventure_documents", "added_by"),
        ("homebrew_entries", "added_by"),
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
        ("homebrew_entries", "created_by"),
        ("house_rules", "created_by"),
        ("recap_holds", "dm_user_id"),
        ("recap_jobs", "dm_user_id"),
        ("recap_must_haves", "created_by"),
        ("recap_style_examples", "created_by"),
        ("recap_style_notes", "created_by"),
        ("recap_upload_rights", "user_id"),
        ("rule_set_editors", "added_by"),
        ("rule_set_follows", "followed_by"),
        ("rule_set_follows", "taken_by"),
        ("rule_set_versions", "created_by"),
        ("rule_sets", "created_by"),
        ("session_imports", "imported_by"),
        ("shared_confirmations", "user_id"),
        ("speaker_removals", "requested_by"),
        ("transcript_suggestions", "decided_by"),
        ("waiting_name_questions", "decided_by"),
    )
)

# Tables the account-deletion tests already cover with real rows (test_account_deletion.py).
ALREADY_COVERED = frozenset(
    {
        "member_directory",
        "question_notices",
        "shared_confirmations",
        "house_rules",
        "rule_sets",
        "rule_set_versions",
        "rule_set_follows",
    }
)

# Rows marked done: some tables allow one open (waiting or working) row per campaign or per
# server, so Alice's and Bob's rows are written as finished (outside that index). Some ids are
# identity columns Postgres generates, so they are not set; recap style rows take their position
# from the Discord ID so each person's row is distinct.
# Each row takes %(g)s (the server), %(c)s (the campaign), %(u)s (the person's Discord ID) and
# %(k)s (a 32-hex id unique to that person). Every required column is set; the rest take
# their defaults.
ROWS: dict[str, str] = {
    "name_actions": (
        "INSERT INTO name_actions (guild_id, campaign_id, id, entity_id, user_id, action,"
        " created_at) VALUES (%(g)s, %(c)s, %(k)s, 'e1', %(u)s, 'name', 1)"
    ),
    "setting_actions": (
        "INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value,"
        " created_at) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 'visibility', 'x', 1)"
    ),
    "web_rule_actions": (
        "INSERT INTO web_rule_actions (guild_id, campaign_id, id, user_id, action,"
        " created_at, updated_at) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 'publish', 1, 1)"
    ),
    "web_report_requests": (
        "INSERT INTO web_report_requests (guild_id, campaign_id, id, user_id, session_at,"
        " created_at, updated_at, state) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, %(u)s, 1, 1, 'done')"
    ),
    "web_restores": (
        "INSERT INTO web_restores (guild_id, id, user_id, name, parts_wanted, created_at,"
        " updated_at) VALUES (%(g)s, %(k)s, %(u)s, 'x', 1, 1, 1)"
    ),
    "web_line_deletions": (
        "INSERT INTO web_line_deletions (guild_id, id, user_id, created_at, updated_at)"
        " VALUES (%(g)s, %(k)s, %(u)s, 1, 1)"
    ),
    "web_line_fixes": (
        "INSERT INTO web_line_fixes (guild_id, campaign_id, id, user_id, session_id,"
        " speaker, started_ms, mode, basis, created_at, updated_at) VALUES (%(g)s, %(c)s, %(k)s,"
        " %(u)s, 's1', 5, 0, 'suggest', %(k)s, 1, 1)"
    ),
    "web_recap_style_actions": (
        "INSERT INTO web_recap_style_actions (guild_id, campaign_id, id, user_id, action,"
        " created_at, updated_at) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 'add_example', 1, 1)"
    ),
    "web_house_file_actions": (
        "INSERT INTO web_house_file_actions (guild_id, campaign_id, id, user_id, action,"
        " created_at, updated_at, state) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 'link', 1, 1, 'done')"
    ),
    "picture_actions": (
        "INSERT INTO picture_actions (guild_id, campaign_id, id, user_id, picture_id,"
        " action, created_at) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 1, 'public', 1)"
    ),
    "script_makes": (
        "INSERT INTO script_makes (guild_id, campaign_id, id, session_id, user_id, created_at)"
        " VALUES (%(g)s, %(c)s, %(k)s, %(k)s, %(u)s, 1)"
    ),
    "trait_answers": (
        "INSERT INTO trait_answers (guild_id, campaign_id, id, user_id, entity_id, kind,"
        " name_key, action, created_at, state) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 'e1', 'species',"
        " 'x', 'keep', 1, 'done')"
    ),
    "upload_undos": (
        "INSERT INTO upload_undos (guild_id, campaign_id, id, user_id, notice_id, created_at,"
        " state)"
        " VALUES (%(g)s, %(c)s, %(k)s, %(u)s, %(k)s, 1, 'done')"
    ),
    "hand_over_answers": (
        "INSERT INTO hand_over_answers (guild_id, campaign_id, id, user_id, older_relation_id,"
        " newer_relation_id, action, created_at, state) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, %(k)s,"
        " %(k)s, 'no', 1, 'done')"
    ),
    "lore_uploads": (
        "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, choice,"
        " rights_by, rights_at, created_at, updated_at, state) VALUES (%(g)s, %(c)s, %(k)s, %(u)s,"
        " 'paste', 'world', %(u)s, 1, 1, 1, 'done')"
    ),
    "web_backups": (
        "INSERT INTO web_backups (guild_id, campaign_id, id, user_id, created_at, updated_at)"
        " VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 1, 1)"
    ),
    "recap_requests": (
        "INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,"
        " created_at) VALUES (%(g)s, %(c)s, %(k)s, 1, %(u)s, 'instructions', 'x', 1)"
    ),
    "web_peeks": (
        "INSERT INTO web_peeks (guild_id, campaign_id, user_id, visit, name, created_at)"
        " VALUES (%(g)s, %(c)s, %(u)s, 'v', 'n', 1)"
    ),
    "npc_notes": (
        "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, body, updated_at)"
        " VALUES (%(g)s, %(c)s, 'e1', %(u)s, 'x', 1)"
    ),
    "homebrew_entries": (
        "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key, text,"
        " created_by, created_at, updated_at, added_by) VALUES (%(g)s, %(c)s, 'spell',"
        " %(k)s, %(k)s, 'x', %(u)s, 1, 1, %(u)s)"
    ),
    "adventure_documents": (
        "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name, fingerprint,"
        " characters, chunks, added_by, added_at) VALUES (%(g)s, %(c)s, %(k)s, 'f.txt',"
        " %(f)s, 1, 0, %(u)s, 1)"
    ),
    "recap_style_examples": (
        "INSERT INTO recap_style_examples (guild_id, campaign_id, position, text,"
        " created_by, created_at) VALUES (%(g)s, %(c)s, %(u)s - 7001, 'x', %(u)s, 1)"
    ),
    "recap_style_notes": (
        "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,"
        " created_at) VALUES (%(g)s, %(c)s, %(u)s - 7001, 'x', %(u)s, 1)"
    ),
    "recap_must_haves": (
        "INSERT INTO recap_must_haves (guild_id, campaign_id, position, text, created_by,"
        " created_at) VALUES (%(g)s, %(c)s, %(u)s - 7001, 'x', %(u)s, 1)"
    ),
    "recap_holds": (
        "INSERT INTO recap_holds (guild_id, campaign_id, session_at, session_id, dm_user_id,"
        " channel_id, created_at) VALUES (%(g)s, %(c)s, %(u)s, 's1', %(u)s, 9, 1)"
    ),
    "speaker_removals": (
        "INSERT INTO speaker_removals (guild_id, campaign_id, id, requested_by, speaker,"
        " created_at, updated_at, state, stage) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 5, 1, 1,"
        " 'done', 'done')"
    ),
    "recap_upload_rights": (
        "INSERT INTO recap_upload_rights (guild_id, campaign_id, user_id, confirmed_at)"
        " VALUES (%(g)s, %(c)s, %(u)s, 1)"
    ),
    "web_sheet_actions": (
        "INSERT INTO web_sheet_actions (guild_id, campaign_id, id, user_id, action, state, stage,"
        " created_at, updated_at) VALUES (%(g)s, %(c)s, %(k)s, %(u)s, 'link', 'done', 'done', 1, 1)"
    ),
    "name_answers": (
        "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action, state,"
        " created_at) VALUES (%(g)s, %(c)s, %(k)s, %(k)s, %(u)s, 'confirm', 'done', 1)"
    ),
    "member_lookups": (
        "INSERT INTO member_lookups (guild_id, id, user_id, target, created_at)"
        " VALUES (%(g)s, %(k)s, %(u)s, %(u)s, 1)"
    ),
    "move_codes": (
        "INSERT INTO move_codes (user_id, code_hash, created_at, expires_at)"
        " VALUES (%(u)s, %(f)s, 1, 2)"
    ),
    "rule_set_editors": (
        "INSERT INTO rule_set_editors (guild_id, campaign_id, set_id, user_id, added_by, added_at)"
        " VALUES (%(g)s, %(c)s, %(k)s, %(u)s, %(u)s, 1)"
    ),
    "adventure_chunks": (
        "INSERT INTO adventure_chunks (guild_id, campaign_id, id, document_id, position, heading,"
        " text, created_at, created_by) VALUES (%(g)s, %(c)s, %(k)s, %(k)s, 0, 'h', 'x', 1,"
        " %(u)s)"
    ),
    "session_imports": (
        "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
        " imported_by, imported_at) VALUES (%(g)s, %(c)s, %(k)s, %(k)s, 'f.txt', %(u)s, 1)"
    ),
    "transcript_suggestions": (
        "INSERT INTO transcript_suggestions (guild_id, campaign_id, session_id, started_ms,"
        " user_id, suggested, base_text, created_at, decided_by) VALUES (%(g)s, %(c)s,"
        " %(k)s, 0, %(u)s, 'x', 'x', 1, %(u)s)"
    ),
    "waiting_name_questions": (
        "INSERT INTO waiting_name_questions (guild_id, campaign_id, session_id, speaker_id,"
        " started_ms, heard, option_ids, created_at, decided_by) VALUES (%(g)s, %(c)s,"
        " %(k)s, %(u)s, 0, 'x', ARRAY['a', 'b'], 1, %(u)s)"
    ),
    "recap_jobs": (
        "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, uploaded_by,"
        " created_at) VALUES (%(g)s, %(c)s, %(u)s, %(u)s, %(u)s, 1)"
    ),
    "house_rule_wins": (
        "INSERT INTO house_rule_wins (guild_id, campaign_id, a_number, b_number, winner,"
        " decided_by, decided_at) VALUES (%(g)s, %(c)s, 1, %(u)s, 'a', %(u)s, 1)"
    ),
    "house_rules_file": (
        "INSERT INTO house_rules_file (guild_id, campaign_id, link, set_by, set_at)"
        " VALUES (%(g)s, %(c)s, 'https://example.com/x', %(u)s, 1)"
    ),
    "pc_icons": (
        "INSERT INTO pc_icons (guild_id, campaign_id, entity_id, choice, set_by, updated_at)"
        " VALUES (%(g)s, %(c)s, %(k)s, 'hidden', %(u)s, 1)"
    ),
    "pack_suggestions_sent": (
        "INSERT INTO pack_suggestions_sent (guild_id, campaign_id, id, set_id, set_name, name,"
        " sent_by, sent_at) VALUES (%(g)s, %(c)s, %(k)s, %(k)s, 'x', 'x', %(u)s, 1)"
    ),
    "rule_set_suggestions_sent": (
        "INSERT INTO rule_set_suggestions_sent (guild_id, campaign_id, id, set_id, set_name, rule,"
        " sent_by, sent_at) VALUES (%(g)s, %(c)s, %(k)s, %(k)s, 'x', 'x', %(u)s, 1)"
    ),
    "site_approvals": (
        "INSERT INTO site_approvals (guild_id, campaign_id, domain, approved_by, approved_at)"
        " VALUES (%(g)s, %(c)s, 'dndbeyond.com', %(u)s, 1)"
    ),
    "recap_accepted": (
        "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version, text_hash, title,"
        " summary, accepted_by, accepted_at)"
        " VALUES (%(g)s, %(c)s, %(u)s, 1, %(f)s, 'x', 'x', %(u)s, 1)"
    ),
    "report_answers": (
        "INSERT INTO report_answers (guild_id, campaign_id, item_key, item_kind, answer,"
        " answered_by, answered_at)"
        " VALUES (%(g)s, %(c)s, %(k)s, 'difference', 'intended', %(u)s, 1)"
    ),
    "plot_fine": (
        "INSERT INTO plot_fine (guild_id, campaign_id, fine_key, note, by_user, at)"
        " VALUES (%(g)s, %(c)s, %(k)s, 'x', %(u)s, 1)"
    ),
}

# The parent rows those tables need, written before ROWS (rule sets, transcript sessions, entries).
PARENTS: dict[str, str] = {
    "memory_entities": (  # (name_answers points at an entry)
        "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description, status,"
        " source, created_at) VALUES (%(g)s, %(c)s, %(k)s, 'npc', %(k)s, '', 'confirmed', 'dm', 1)"
    ),
    "rule_sets": (
        "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
        " created_at) VALUES (%(g)s, %(c)s, %(k)s, 'house_rules', 'x', 'shared', %(u)s, 1)"
    ),
    "transcript_sessions": (
        "INSERT INTO transcript_sessions (guild_id, campaign_id, id, started_at)"
        " VALUES (%(g)s, %(c)s, %(k)s, 1)"
    ),
}

# Empty: the second PR of #4162 covered the parent-row tables and recap_jobs (above). The
# completeness test below still checks that every forgotten table is covered somewhere.
NOT_YET: frozenset[str] = frozenset()

# Tables the same-campaign test (#4181) cannot give Bob's second row in Alice's campaign: a key that
# already has Bob's row, one row per campaign, or a parent row with a clashing key. Each has its
# reason beside it.
SAME_CAMPAIGN_SKIPS: frozenset[str] = frozenset(
    {
        # recap_upload_rights: one row per campaign (key: server and campaign), and Alice's setUp
        # row already holds Alice's campaign's one.
        "recap_upload_rights",
        # house_rules_file and site_approvals: keyed by the campaign (one file link) or by the
        # campaign and the site (dndbeyond.com), and Alice's campaign already holds that row.
        "house_rules_file",
        "site_approvals",
        # rule_sets: a campaign has one house-rules set, and Alice's campaign already has it, so
        # Bob's parent row cannot go in; rule_set_editors points at that row, so it is skipped too.
        "rule_sets",
        "rule_set_editors",
        # transcript_sessions: unique on (campaign, started_at), and Bob's parent row has the
        # same fixed start time as Alice's; the three tables that point at a session are skipped.
        "transcript_sessions",
        "session_imports",
        "transcript_suggestions",
        "waiting_name_questions",
    }
)

# Bob's second row in Alice's campaign: its own id, distinct from both people's rows.
SECOND_ID = f"{BOB + 0x10000:032x}"
SECOND_FINGERPRINT = f"{BOB + 0x10000:064x}"


class AccountDeletionTables(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        entitlements.configure_free_users((BOB,))  # BOB has room to take a campaign on
        self.store = CampaignStore(self.db, clock=lambda: NOW)
        for user in (ALICE, BOB):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        # Each person has a campaign of their own: some tables allow one row per campaign.
        campaigns = {
            ALICE: await self.store.create(GUILD_A, "Alice one", ALICE),
            BOB: await self.store.create(GUILD_A, "Bob one", BOB),
        }
        self.campaigns = campaigns
        async with self.db.guild(GUILD_A) as conn:
            for sql in [*PARENTS.values(), *ROWS.values()]:
                for user in (ALICE, BOB):
                    await conn.execute(
                        sql,
                        {
                            "g": GUILD_A,
                            "c": campaigns[user].id,
                            "u": user,
                            "k": f"{user:032x}",
                            "f": f"{user:064x}",
                        },
                    )

    async def count(self, table: str, column: str, value: int) -> int:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE {column} = %s", (value,)
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_every_deleted_table_loses_the_persons_rows_and_keeps_the_others(self) -> None:
        for table, column in DELETED:
            if table in ROWS:  # (before the deletion, Alice's row is there: no vacuous pass)
                self.assertEqual(await self.count(table, column, ALICE), 1, table)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table, column in DELETED:
            if table not in ROWS:
                continue
            with self.subTest(table=table):
                self.assertEqual(await self.count(table, column, ALICE), 0)
                self.assertEqual(await self.count(table, column, BOB), 1)

    async def rows(self, table: str, column: str, value: int) -> list[dict[str, object]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                f"SELECT * FROM {table} WHERE {column} = %s ORDER BY 1", (value,)
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_an_editor_alice_added_stays_with_nobody_as_the_adder(self) -> None:
        # Bob is an editor of Alice's rule set, which Alice added: his row stays, the adder goes.
        alice_campaign = self.campaigns[ALICE].id
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO rule_set_editors (guild_id, campaign_id, set_id, user_id, added_by,"
                " added_at) VALUES (%s, %s, %s, %s, %s, 1)",
                (GUILD_A, alice_campaign, f"{ALICE:032x}", BOB, ALICE),
            )
            cur = await conn.execute(
                "SELECT added_by FROM rule_set_editors WHERE campaign_id = %s AND user_id = %s",
                (alice_campaign, BOB),
            )
            self.assertEqual([r["added_by"] for r in await cur.fetchall()], [ALICE])
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT user_id, added_by FROM rule_set_editors"
                " WHERE campaign_id = %s AND user_id = %s",
                (alice_campaign, BOB),
            )
            rows = [(r["user_id"], r["added_by"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [(BOB, 0)])

    async def test_every_zeroed_column_goes_to_zero_and_the_row_stays(self) -> None:
        # (rule_set_editors is also deleted by its user: its adder is tested on its own above)
        deleted = {table for table, _ in DELETED}
        pairs = [(table, column) for table, column in ZEROED if table not in deleted]
        alices = {table: await self.rows(table, column, ALICE) for table, column in pairs}
        for table in alices:
            if table in ROWS:  # (before the deletion, Alice's row is there: no vacuous pass)
                self.assertEqual(len(alices[table]), 1, table)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table, column in pairs:
            if table not in ROWS:
                continue
            with self.subTest(table=table, column=column):
                self.assertEqual(await self.count(table, column, 0), 1)
                self.assertEqual(await self.count(table, column, BOB), 1)
                # the rest of the row is what it was (its zeroed columns aside)
                zeroed = {c for t, c in ZEROED if t == table}
                before = [{k: v for k, v in r.items() if k not in zeroed} for r in alices[table]]
                after = await self.rows(table, column, 0)
                self.assertEqual(
                    before, [{k: v for k, v in r.items() if k not in zeroed} for r in after]
                )

    async def test_every_forgotten_table_has_a_row_test(self) -> None:
        forgotten = {t for t, _ in DELETED} | {t for t, _ in ZEROED}
        self.assertEqual(forgotten, set(ROWS) | set(NOT_YET) | set(ALREADY_COVERED))
        self.assertEqual(NOT_YET, frozenset())  # the second PR emptied it
        self.assertFalse(set(ROWS) & set(NOT_YET))
        self.assertFalse(set(ROWS) & ALREADY_COVERED)
        self.assertFalse(set(NOT_YET) & ALREADY_COVERED)

    async def count_in_campaign(self, table: str, column: str, value: int, campaign: str) -> int:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE {column} = %s AND campaign_id = %s",
                (value, campaign),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_the_forget_lists_are_the_ones_pinned_here(self) -> None:
        self.assertEqual(set(DELETED), EXPECTED_DELETED)
        self.assertEqual(set(ZEROED), EXPECTED_ZEROED)

    async def test_a_second_persons_row_in_the_same_campaign_survives(self) -> None:
        alice_campaign = self.campaigns[ALICE].id
        written: list[tuple[str, tuple[str, ...]]] = []
        params = {
            "g": GUILD_A,
            "c": alice_campaign,
            "u": BOB,
            "k": SECOND_ID,
            "f": SECOND_FINGERPRINT,
        }
        async with self.db.guild(GUILD_A) as conn:
            # (an entry for name_answers to point at: the only parent whose key does not clash)
            await conn.execute(PARENTS["memory_entities"], params)
            for table, sql in ROWS.items():
                if table in PARENTS or table in SAME_CAMPAIGN_SKIPS or "campaign_id" not in sql:
                    continue
                columns = tuple(c for t, c in (*DELETED, *ZEROED) if t == table)
                self.assertTrue(columns, table)
                await conn.execute(sql, params)
                written.append((table, columns))
        # Every forgotten table with a row here and a campaign column is written, or skipped above.
        expected = {
            table
            for table, _ in (*DELETED, *ZEROED)
            if table in ROWS
            and table not in PARENTS
            and table not in SAME_CAMPAIGN_SKIPS
            and "campaign_id" in ROWS[table]
        }
        self.assertEqual({table for table, _ in written}, expected)
        for table, columns in written:  # (before the deletion, both people's rows are there)
            for column in columns:
                with self.subTest(table=table, column=column, before=True):
                    self.assertEqual(
                        await self.count_in_campaign(table, column, ALICE, alice_campaign), 1
                    )
                    self.assertEqual(
                        await self.count_in_campaign(table, column, BOB, alice_campaign), 1
                    )
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table, columns in written:
            for column in columns:
                with self.subTest(table=table, column=column, after=True):
                    self.assertEqual(
                        await self.count_in_campaign(table, column, BOB, alice_campaign), 1
                    )
