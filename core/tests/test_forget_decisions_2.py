"""The third set of "who decided it" columns goes to 0 when a person's account is deleted (0493,
#4148): the record stays, its person is nobody, and the other person's rows are untouched. One
campaign in its own server for each person, with a row in each of the eight tables. Plot answers
are also in a backup, so a backup taken afterwards holds "0" and must restore. Needs Postgres."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from dmbot import entitlements
from dmbot.campaigns import CampaignStore
from dmbot.recaps.plot_backup import PlotIssuesSection
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB = 7101, 7102
NOW = 1_800_000_000
FORGET = import_module("dmbot.migrations.0493_forget_decisions_2")
A, B = "0" * 31 + "1", "0" * 31 + "2"  # two entries, in the order the key needs

COLUMNS = {
    "name_not_same": "decided_by",
    "plan_same_as": "decided_by",
    "plan_position": "set_by",
    "pack_suggestions": "resolved_by",
    "rule_set_suggestions": "resolved_by",
    "plot_issues": "answered_by",
    "adventure_reads": "started_by",
    "active_sessions": "started_by",
}


class ForgetDecisionsTwo(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        entitlements.configure_free_users((ALICE, BOB))
        self.store = CampaignStore(self.db, clock=lambda: NOW)
        self.guild = {ALICE: 111, BOB: 222}
        self.campaign: dict[int, str] = {}
        for user, guild in self.guild.items():
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
            self.campaign[user] = (await self.store.create(guild, f"c{user}", user)).id
            await self.rows_for(user)

    async def rows_for(self, user: int) -> None:
        p = {
            "g": self.guild[user], "c": self.campaign[user], "u": user, "a": A, "b": B,
            "r": f"{user:032x}", "s": f"{user + 0x1000:032x}",  # (ids that are unique everywhere)
        }  # fmt: skip
        async with self.db.guild(self.guild[user]) as conn:
            for sql in (
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%(g)s, %(c)s, %(a)s, 'npc', 'a', '',"
                " 'confirmed', 'dm', 1), (%(g)s, %(c)s, %(b)s, 'npc', 'b', '', 'confirmed',"
                " 'dm', 1)",
                "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name,"
                " fingerprint, characters, chunks, added_by, added_at) VALUES (%(g)s, %(c)s, %(a)s,"
                " 'f.txt', repeat('a', 64), 5, 1, 0, 1)",
                "INSERT INTO plan_nodes (guild_id, campaign_id, id, document_id, kind, name,"
                " name_key, summary, quote, chunk_ids, position, created_at) VALUES (%(g)s, %(c)s,"
                " %(a)s, %(a)s, 'chapter', 'n', 'n', '', '', ARRAY[]::text[], 0, 1)",
                "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
                " created_at) VALUES (%(g)s, %(c)s, %(r)s, 'house_rules', 'x', 'shared', 0, 1)",
                "INSERT INTO name_not_same (guild_id, campaign_id, a_id, b_id, decided_by,"
                " created_at) VALUES (%(g)s, %(c)s, %(a)s, %(b)s, %(u)s, 1)",
                "INSERT INTO plan_same_as (guild_id, campaign_id, id, plan_id, document_id,"
                " entity_id, status, decided_by, created_at) VALUES (%(g)s, %(c)s, %(a)s, %(a)s,"
                " %(a)s, %(a)s, 'same', %(u)s, 1)",
                "INSERT INTO plan_position (guild_id, campaign_id, id, document_id, chapter_id,"
                " set_by, created_at) VALUES (%(g)s, %(c)s, %(a)s, %(a)s, %(a)s, %(u)s, 1)",
                "INSERT INTO pack_suggestions (guild_id, campaign_id, id, set_id, kind, name, text,"
                " known, status, created_at, resolved_by) VALUES (%(g)s, %(c)s, %(r)s, %(r)s,"
                " 'spell', 'n', 't', false, 'accepted', 1, %(u)s),"
                " (%(g)s, %(c)s, %(s)s, %(r)s, 'spell', 'm', 't', false, 'open', 1, NULL)",
                "INSERT INTO rule_set_suggestions (guild_id, campaign_id, id, set_id, rule, status,"
                " created_at, resolved_by) VALUES (%(g)s, %(c)s, %(r)s, %(r)s, 'r', 'accepted', 1,"
                " %(u)s)",
                "INSERT INTO adventure_reads (guild_id, campaign_id, document_id, status,"
                " next_chunk, total_chunks, started_by, started_at, updated_at) VALUES (%(g)s,"
                " %(c)s, %(a)s, 'done', 0, 1, %(u)s, 1, 1)",
                "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
                " screen_channel_id, started_by, started_at) VALUES (%(g)s, %(c)s, 1, 2, %(u)s, 1)",
            ):
                await conn.execute(sql, p)
            await self.plot_issue(conn, p)

    @staticmethod
    async def plot_issue(conn: Any, p: dict[str, Any]) -> None:
        await conn.execute(
            "INSERT INTO plot_issues (guild_id, campaign_id, id, session_at, text_hash, kind,"
            " level, sentence, sentence_text, reason, source_text, fine_key, created_at,"
            " answer_kind, answered_by, answered_at) VALUES (%(g)s, %(c)s, %(a)s, 1, %(a)s,"
            " 'place', 'possible', 1, 's', 'r', 'q', 'k', 1, 'fine', %(u)s, 2)",
            p,
        )

    async def column(self, user: int, table: str) -> list[int | None]:
        async with self.db.guild(self.guild[user]) as conn:
            cur = await conn.execute(f"SELECT {COLUMNS[table]} AS who FROM {table} ORDER BY 1")
            return [r["who"] for r in await cur.fetchall()]

    async def test_the_list_is_the_one_pinned_here(self) -> None:
        self.assertEqual(dict(FORGET.ZEROED), COLUMNS)

    async def test_each_column_goes_to_zero_the_row_stays_and_bobs_rows_do_not_change(self) -> None:
        for table in COLUMNS:
            self.assertIn(ALICE, await self.column(ALICE, table), table)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table in COLUMNS:
            with self.subTest(table=table):
                after = await self.column(ALICE, table)
                self.assertNotIn(ALICE, after)
                self.assertEqual(after.count(0), 1)  # (the row stays; a NULL one stays NULL)
        for table in COLUMNS:
            self.assertIn(BOB, await self.column(BOB, table), table)

    async def test_an_unresolved_suggestion_keeps_its_empty_resolver(self) -> None:
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        self.assertEqual(await self.column(ALICE, "pack_suggestions"), [0, None])

    async def test_a_backup_after_the_deletion_restores_a_plot_answer_by_nobody(self) -> None:
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        section = PlotIssuesSection()
        async with self.db.guild(self.guild[ALICE]) as conn:
            rows = await section.dump(conn, self.guild[ALICE], self.campaign[ALICE])
        self.assertEqual([r["answered_by"] for r in rows], ["0"])
        async with self.db.guild(self.guild[ALICE]) as conn:
            section.check(rows)  # (the checker accepts "someone who has left")
