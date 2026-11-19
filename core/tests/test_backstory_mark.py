"""Who-knows rows written for a backstory say whose backstory, and no copy passes one character's
backstory to another (#4795, sub-issue of #4537; migration 0556; Supervisor's ruling on #4794;
`docs/design/party_knowledge.md` §5). Invented names and ids only."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED, DM
from tests.test_party_join import CARA, GUILD, Party


def _backfill() -> str:
    """0556's backfill statement alone (its policies are the migration's; here the test's own
    server scope lets it see the rows)."""
    import importlib

    sql = str(importlib.import_module("dmbot.migrations.0556_knowers_backstory_of").SQL)
    start = sql.index("WITH written AS")
    return sql[start : sql.index(";", sql.index("UPDATE memory_knowers k"))]


class BackstoryRows(Party):
    """Vell's backstory names a sister only Vell knows; Vell and Tuck know the harbourmaster."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_entity(
            GUILD, self.c, name="Ysolde", type="npc", status=CONFIRMED, source=DM
        )
        self.sister = str(made.value.id)
        rel = await self.memory.add_relation(
            GUILD, self.c, self.vell, "knows", self.sister, source=DM, confidence=1.0,
            status=CONFIRMED, unrevealed=True, known_by=[self.vell], backstory_of=self.vell,
        )  # fmt: skip
        self.tie = str(rel.value[0].id)

    async def marks(self) -> dict[tuple[str, str], Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id, k.relation_id, k.backstory_of FROM memory_knowers k"
                " JOIN memory_perspectives p ON p.guild_id = k.guild_id"
                " AND p.campaign_id = k.campaign_id AND p.id = k.perspective_id"
                " WHERE k.guild_id = %s AND k.campaign_id = %s AND k.relation_id IS NOT NULL",
                (GUILD, self.c),
            )
            return {
                (r["entity_id"], r["relation_id"]): r["backstory_of"] for r in await cur.fetchall()
            }

    async def two_with_cara(self) -> str:
        """Tuck leaves; Cara joins a party of two and is told only what everyone knows."""
        await self.memory.leave_party(GUILD, self.c, self.tuck, "left")
        cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        self.group = q.group_id
        return cara

    async def test_the_keep_marks_its_characters_row(self) -> None:
        self.assertEqual((await self.marks())[(self.vell, self.tie)], self.vell)

    async def test_a_party_of_two_never_copies_the_others_backstory(self) -> None:
        cara = await self.two_with_cara()
        await self.memory.join_answer_party(GUILD, self.c, self.group)
        self.assertTrue(await self.sees(cara, "entry", self.npc))  # (what both know: copied)
        self.assertNotIn((cara, self.tie), await self.marks())  # (Vell's backstory: never)

    async def test_catch_up_in_a_party_of_two_brings_what_is_shared_not_a_backstory(self) -> None:
        cara = await self.two_with_cara()
        await self.memory.join_answer_everyone(GUILD, self.c, self.group)
        offered = await self.memory.tell_choices(GUILD, self.c, self.vell)  # (Vell's own card)
        self.assertEqual([m for m, _, _ in offered], [cara])  # (a party of two works again)
        await self.memory.tell_member(GUILD, self.c, cara, self.vell)
        self.assertTrue(await self.sees(cara, "entry", self.vell))
        self.assertNotIn((cara, self.tie), await self.marks())

    async def test_one_who_learned_it_at_the_table_makes_it_the_partys(self) -> None:
        # (Tuck heard about the sister in play: unmarked, so the newcomer may be given it)
        held = await self.memory.connection_knowers(GUILD, self.c, [self.tie])
        await self.memory.set_statement_knowers(
            GUILD, self.c, "connection", self.tie, [*held.get(self.tie, []), self.tuck],
            source=DM, dm_chose_secret=True,
        )  # fmt: skip
        cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertIn((cara, self.tie), await self.marks())
        self.assertIsNone((await self.marks())[(cara, self.tie)])  # (Cara's own row: unmarked)

    async def test_the_backfill_marks_a_traced_row_and_leaves_the_rest(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (as before the migration: nothing marked)
            await conn.execute(
                "UPDATE memory_knowers SET backstory_of = NULL WHERE guild_id = %s", (GUILD,)
            )
            await conn.execute(_backfill())
        marks = await self.marks()
        self.assertEqual(marks[(self.vell, self.tie)], self.vell)  # (one character, hidden, born)
        self.assertTrue(all(v is None for k, v in marks.items() if k[1] != self.tie))

    async def entry_marks(self, entry: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id, k.backstory_of FROM memory_knowers k"
                " JOIN memory_perspectives p ON p.guild_id = k.guild_id"
                " AND p.campaign_id = k.campaign_id AND p.id = k.perspective_id"
                " WHERE k.guild_id = %s AND k.campaign_id = %s AND k.about_entity_id = %s",
                (GUILD, self.c, entry),
            )
            return {r["entity_id"]: r["backstory_of"] for r in await cur.fetchall()}

    async def hidden_name(self) -> str:
        """A name only Vell's backstory knows (made hidden, so nobody learns it by its flags)."""
        made = await self.memory.add_entity(
            GUILD, self.c, name="The Gray Hand", type="faction", status=CONFIRMED, source=DM
        )
        hand = str(made.value.id)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM memory_knowers WHERE guild_id = %s AND about_entity_id = %s",
                (GUILD, hand),
            )
        return hand

    async def test_a_backstorys_names_are_marked_and_never_copied(self) -> None:
        hand = await self.hidden_name()
        await self.memory.learn_statements(
            GUILD, self.c, "entry", [(hand, self.vell)], source=DM, backstory=True
        )
        self.assertEqual(await self.entry_marks(hand), {self.vell: self.vell})
        cara = await self.two_with_cara()
        await self.memory.join_answer_party(GUILD, self.c, self.group)
        self.assertNotIn(cara, await self.entry_marks(hand))

    async def test_a_row_learned_before_is_not_marked(self) -> None:
        hand = await self.hidden_name()
        await self.memory.learn_statements(GUILD, self.c, "entry", [(hand, self.vell)], source=DM)
        await self.memory.learn_statements(
            GUILD, self.c, "entry", [(hand, self.vell)], source=DM, backstory=True
        )
        self.assertEqual(await self.entry_marks(hand), {self.vell: None})  # (heard it first)

    async def test_a_secret_names_row_is_marked(self) -> None:
        await self.memory.add_alias(
            GUILD, self.c, self.sister, "the Widow", kind="title", secret=True, source=DM,
            status=CONFIRMED,
        )  # fmt: skip
        [alias] = [
            a
            for a in await self.memory.aliases(
                GUILD, self.c, entity_id=self.sister, include_secret=True
            )
            if a.secret
        ]
        await self.memory.set_alias_knowers(
            GUILD, self.c, alias.id, [self.vell], source=DM, dm_chose_secret=False,
            backstory_of=self.vell,
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT backstory_of FROM memory_knowers WHERE guild_id = %s AND alias_id = %s",
                (GUILD, alias.id),
            )
            self.assertEqual([r["backstory_of"] for r in await cur.fetchall()], [self.vell])

    async def test_a_list_saved_over_a_sleeping_row_keeps_its_mark(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (a hide put Vell's backstory row to sleep)
            await conn.execute(
                "UPDATE memory_knowers SET hidden_by = ARRAY[%s] WHERE guild_id = %s"
                " AND relation_id = %s",
                (self.sister, GUILD, self.tie),
            )
        await self.memory.set_statement_knowers(
            GUILD, self.c, "connection", self.tie, [self.vell], source=DM, dm_chose_secret=True
        )
        self.assertEqual((await self.marks())[(self.vell, self.tie)], self.vell)

    async def test_the_backfill_marks_a_backstorys_names(self) -> None:
        hand = await self.hidden_name()
        await self.memory.learn_statements(GUILD, self.c, "entry", [(hand, self.vell)], source=DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(_backfill())
        self.assertEqual(await self.entry_marks(hand), {self.vell: self.vell})

    async def test_a_backup_carries_the_mark(self) -> None:
        from dmbot.memory.backup import MemorySection

        async with self.db.guild(GUILD) as conn:
            dumped = await MemorySection().dump(conn, GUILD, self.c)
        rows = [r for r in dumped if isinstance(r, dict) and r.get("table") == "knower"]
        self.assertIn(self.vell, {r.get("backstory_of") for r in rows})
