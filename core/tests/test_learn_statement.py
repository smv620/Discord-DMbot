"""One character learns one statement (#2996, part 2 slice 1, PR 2): the write a kept "who learned
what" proposal makes. One row at that character's own point of view, sure; nobody else changes
("Bruno tells Tran": Clyde learns nothing); never lowers a certainty; one Undo; the gate shows it to
the character's player only after it. Invented names; needs Postgres."""

from __future__ import annotations

from dmbot.memory.gate import CONNECTION, ENTRY, Viewer
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
        self.assertTrue(await self.learn(self.tran, ENTRY, self.ids["sword"]))
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.tran))
        self.assertTrue(seen.knows(ENTRY, self.ids["sword"]))

    async def test_only_the_dm_only_a_character_and_only_a_statement_that_is_here(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.learn_statement(
                GUILD_A, self.c, CONNECTION, self.rel, self.tran, source="scan"
            )
        group = await self.add("The Irendi", type="faction", status=CONFIRMED)
        for bad in (group, self.ids["sword"], "0" * 32):  # a group, an item, nowhere
            with self.subTest(bad), self.assertRaises(MemoryRuleError):
                await self.learn(bad)
        for kind, statement in ((CONNECTION, "0" * 32), ("nonsense", self.rel)):
            with self.subTest(kind), self.assertRaises(MemoryRuleError):
                await self.learn(self.tran, kind, statement)
        self.assertEqual(await self.knowers(), [])

    async def test_another_server_never_reaches_it(self) -> None:
        other = (await self.campaigns.create(GUILD_B, "Elsewhere", 9)).id
        with self.assertRaises(MemoryRuleError):
            await self.memory.learn_statement(
                GUILD_B, other, CONNECTION, self.rel, self.tran, source="dm"
            )
        self.assertEqual(await self.knowers(), [])
