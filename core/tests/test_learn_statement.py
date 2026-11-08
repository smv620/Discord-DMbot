"""One character learns one statement (#2996, part 2 slice 1, PR 2): the write a kept "who learned
what" proposal makes. One row at that character's own point of view, sure; nobody else changes
("Bruno tells Tran": Clyde learns nothing); never lowers a certainty; one Undo; the gate shows it to
the character's player only after it. Invented names; needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, Viewer
from dmbot.memory.knowers import NO_SUCH_STATEMENT, NOT_A_CHARACTER, NOT_ONE_CHARACTER, ONLY_THE_DM
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_gate import Helpers
from tests.test_memory_store import GUILD_A, GUILD_B


class Learning(Helpers):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids = await self.table()
        self.tran = await self.add("Tran", type="character", role="npc", status=CONFIRMED)
        self.clyde = await self.add("Clyde", type="character", role="npc", status=CONFIRMED)
        self.rel = await self.link(self.ids["bruno"], self.ids["shela"])  # (a secret)

    async def learn(self, who: str, kind: str = CONNECTION, statement: str = "") -> bool:
        done = await self.memory.learn_statement(
            GUILD_A, self.c, kind, statement or self.rel, who, source="dm"
        )
        return done.value

    async def knowers(self) -> list[str]:
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [self.rel])
        return sorted(got.get(self.rel, []))

    async def test_bruno_tells_tran_and_clyde_learns_nothing(self) -> None:
        await self.know(CONNECTION, self.rel, [self.ids["bruno"]])
        self.assertTrue(await self.learn(self.tran))
        self.assertEqual(await self.knowers(), sorted([self.ids["bruno"], self.tran]))
        tran = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.tran))
        self.assertTrue(tran.knows(CONNECTION, self.rel))
        self.assertTrue({self.ids["bruno"], self.ids["shela"]} <= tran.entries)  # (its ends)
        clyde = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.clyde))
        self.assertFalse(clyde.knows(CONNECTION, self.rel))
        self.assertNotIn(self.ids["shela"], clyde.entries)

    async def test_a_player_sees_it_only_after_it_is_kept(self) -> None:
        player = Viewer.player({self.ids["okthandom"]})
        before = await self.memory.visible_to(GUILD_A, self.c, player)
        self.assertFalse(before.knows(CONNECTION, self.rel))
        await self.learn(self.ids["okthandom"])
        after = await self.memory.visible_to(GUILD_A, self.c, player)
        self.assertTrue(after.knows(CONNECTION, self.rel))
        mara = await self.memory.visible_to(GUILD_A, self.c, Viewer.player({self.ids["mara"]}))
        self.assertFalse(mara.knows(CONNECTION, self.rel))  # (only that character's player)
        party = await self.memory.visible_to(GUILD_A, self.c, Viewer.the_party())
        self.assertFalse(party.knows(CONNECTION, self.rel))

    async def test_learning_twice_changes_nothing_and_never_lowers_a_certainty(self) -> None:
        self.assertTrue(await self.learn(self.tran))
        self.assertFalse(await self.learn(self.tran))
        self.assertEqual(await self.knowers(), [self.tran])
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, self.rel, {self.clyde: 0.4}, source="dm"
        )
        self.assertTrue(await self.learn(self.clyde))  # (raised to sure)
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.clyde))
        self.assertEqual(seen.how_sure(CONNECTION, self.rel), 1.0)

    async def test_one_undo_takes_it_back(self) -> None:
        await self.know(CONNECTION, self.rel, [self.ids["bruno"]])
        done = await self.memory.learn_statement(
            GUILD_A, self.c, CONNECTION, self.rel, self.tran, source="dm"
        )
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(await self.knowers(), [self.ids["bruno"]])

    async def test_an_entry_can_be_learned(self) -> None:
        before = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.tran))
        self.assertFalse(before.knows(ENTRY, self.ids["sword"]))
        self.assertTrue(await self.learn(self.tran, ENTRY, self.ids["sword"]))
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.tran))
        self.assertTrue(seen.knows(ENTRY, self.ids["sword"]))

    async def test_only_the_dm_only_a_character_and_only_a_statement_that_is_here(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, ONLY_THE_DM):
            await self.memory.learn_statement(
                GUILD_A, self.c, CONNECTION, self.rel, self.tran, source="scan"
            )
        group = await self.add("The Irendi", type="faction", status=CONFIRMED)
        with self.assertRaisesRegex(MemoryRuleError, NOT_ONE_CHARACTER):
            await self.learn(group)  # (a group, and so never the party)
        for bad in (self.ids["sword"], "0" * 32):  # an item, nowhere
            with self.subTest(bad), self.assertRaisesRegex(MemoryRuleError, NOT_A_CHARACTER):
                await self.learn(bad)
        for kind, statement in ((CONNECTION, "0" * 32), ("nonsense", self.rel)):
            with self.subTest(kind), self.assertRaisesRegex(MemoryRuleError, NO_SUCH_STATEMENT):
                await self.learn(self.tran, kind, statement)
        self.assertEqual(await self.knowers(), [])

    async def test_a_rejected_statement_is_refused(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET status = 'rejected'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.c, self.rel),
            )
        with self.assertRaisesRegex(MemoryRuleError, NO_SUCH_STATEMENT):
            await self.learn(self.tran)

    async def test_the_dm_s_who_knows_menu_shows_it_and_saving_it_keeps_it(self) -> None:
        pc, mara = self.ids["okthandom"], self.ids["mara"]
        await self.learn(pc)
        menu = await self.memory.connection_knowers(GUILD_A, self.c, [self.rel])
        self.assertEqual(menu[self.rel], [pc])  # (the old reader sees it too)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, self.rel, [*menu[self.rel], mara], source="dm"
        )  # (the DM adds Mara from the menu, which was pre-filled with what it read)
        self.assertEqual(await self.knowers(), sorted([pc, mara]))
        self.assertFalse(await self.learn(pc))  # (sure in every table: nothing to change)

    async def test_a_secret_other_name_is_listed_where_the_name_card_reads(self) -> None:
        name = await self.memory.add_alias(
            GUILD_A, self.c, self.ids["bruno"], "The Masked One", kind="nickname", source="dm",
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        alias, pc = name.value.id, self.ids["okthandom"]
        self.assertTrue(await self.learn(pc, ALIAS, alias))
        listed = await self.memory.alias_knowers(GUILD_A, self.c, [alias])
        self.assertEqual(listed[alias], [pc])
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.player({pc}))
        self.assertTrue(seen.knows(ALIAS, alias))
        self.assertFalse(await self.learn(pc, ALIAS, alias))

    async def test_a_row_a_hide_put_to_sleep_is_replaced_for_that_character_only(self) -> None:
        pc, mara = self.ids["okthandom"], self.ids["mara"]  # (a hide sleeps the players' rows)
        arlo = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        self.rel = await self.link(arlo, self.ids["shela"])  # (an end the players can see)
        await self.memory.set_connection_knowers(GUILD_A, self.c, self.rel, [pc, mara], source="dm")
        await self.memory.set_hidden_from_players(GUILD_A, self.c, arlo, True, source="dm")
        asleep = await self.rows()
        self.assertTrue(asleep[pc]["hidden_by"] and asleep[mara]["hidden_by"])
        done = await self.memory.learn_statement(
            GUILD_A, self.c, CONNECTION, self.rel, pc, source="dm"
        )
        rows = await self.rows()
        self.assertEqual((rows[pc]["hidden_by"], rows[pc]["confidence"]), ([], 1.0))
        self.assertEqual(rows[mara]["hidden_by"], asleep[mara]["hidden_by"])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(await self.rows(), asleep)

    async def rows(self) -> dict[str, Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id, k.confidence, k.hidden_by FROM memory_knowers k"
                " JOIN memory_perspectives p ON p.guild_id = k.guild_id"
                " AND p.campaign_id = k.campaign_id AND p.id = k.perspective_id"
                " WHERE k.guild_id = %s AND k.campaign_id = %s AND k.relation_id = %s",
                (GUILD_A, self.c, self.rel),
            )
            return {
                str(r["entity_id"]): {"confidence": r["confidence"], "hidden_by": r["hidden_by"]}
                for r in await cur.fetchall()
            }

    async def test_another_server_never_reaches_it(self) -> None:
        other = (await self.campaigns.create(GUILD_B, "Elsewhere", 9)).id
        with self.assertRaises(MemoryRuleError):
            await self.memory.learn_statement(
                GUILD_B, other, CONNECTION, self.rel, self.tran, source="dm"
            )
        self.assertEqual(await self.knowers(), [])
