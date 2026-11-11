"""Every table account deletion changes is tested with real rows (#4162, first PR): one row for
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
DELETED: tuple[tuple[str, str], ...] = FORGET_PERSON.DELETED
ZEROED: tuple[tuple[str, str], ...] = FORGET_PERSON.ZEROED + FORGET_AUTHORSHIP.ZEROED

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

# Each row takes :g (the server), :c (the campaign), :u (the person's Discord ID) and :k (a
# 32-hex id unique to that person). Every required column is set; the rest take their defaults.
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
}

# The second PR of #4162 covers these (parent rows: a rule set, a document, a transcript session;
# and recap_jobs). Empty in that PR, which asserts it.
NOT_YET: frozenset[str] = frozenset(
    {
        "rule_set_editors",  # covered in the second PR of #4162
        "adventure_chunks",  # covered in the second PR of #4162
        "session_imports",  # covered in the second PR of #4162
        "transcript_suggestions",  # covered in the second PR of #4162
        "waiting_name_questions",  # covered in the second PR of #4162
        "recap_jobs",  # covered in the second PR of #4162
    }
)


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
        async with self.db.guild(GUILD_A) as conn:
            for sql in ROWS.values():
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
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table, column in DELETED:
            if table not in ROWS:
                continue
            with self.subTest(table=table):
                self.assertEqual(await self.count(table, column, ALICE), 0)
                self.assertEqual(await self.count(table, column, BOB), 1)

    async def rows(self, table: str, column: str, value: int) -> list[dict[str, object]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(f"SELECT * FROM {table} WHERE {column} = %s", (value,))
            return [dict(r) for r in await cur.fetchall()]

    async def test_every_zeroed_column_goes_to_zero_and_the_row_stays(self) -> None:
        alices = {table: await self.rows(table, column, ALICE) for table, column in ZEROED}
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table, column in ZEROED:
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
        self.assertFalse(set(ROWS) & set(NOT_YET))
