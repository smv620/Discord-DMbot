"""Joining an unnamed person to the named one it turned out to be (#3037): nothing widens. Whoever
knew only "the guard at the gate" knows that secret name and not "Ken"; whoever knew Ken keeps
what they knew; the DM sees both; Undo puts both back. Invented names."""

from __future__ import annotations

from dmbot.memory.gate import ALIAS, ENTRY, Viewer
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_gate import Helpers
from tests.test_memory_store import GUILD_A


class TheJoin(Helpers):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids = await self.table()
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="guard at the north gate", role="guard"
        )
        self.guard = made.entity.id
        self.ken = await self.add("Ken", type="character", role="npc", status=CONFIRMED)
        (shown,) = await self.memory.unnamed(GUILD_A, self.c)
        self.label = shown[0].name  # (what the join makes the secret other name from)
        self.sees_guard = self.ids["okthandom"]  # knew only the unnamed guard
        self.sees_ken = self.ids["mara"]  # knew Ken
        await self.know(ENTRY, self.guard, [self.sees_guard])
        await self.know(ENTRY, self.ken, [self.sees_ken])

    async def join(self) -> object:
        return await self.memory.join_same_person(GUILD_A, self.c, self.guard, self.ken)

    async def view(self, pc: str) -> object:
        return await self.memory.visible_to(GUILD_A, self.c, Viewer.character(pc))

    async def alias_id(self) -> str:
        found = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        (one,) = [a for a in found if a.text == self.label]
        return one.id

    async def test_the_description_becomes_a_secret_other_name_of_the_named_entry(self) -> None:
        await self.join()
        found = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        (alias,) = [a for a in found if a.text == self.label]
        self.assertEqual((alias.secret, alias.status), (True, CONFIRMED))
        public = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken)
        self.assertNotIn(self.label, [a.text for a in public])  # never shown to players

    async def test_who_knew_only_the_guard_knows_the_secret_name_and_not_ken(self) -> None:
        await self.join()
        seen = await self.view(self.sees_guard)
        # (the entry is known to them only through that name: no row says they know it by its own)
        self.assertNotIn((ENTRY, self.ken), seen.sureness)  # type: ignore[attr-defined]
        alias = await self.alias_id()
        self.assertTrue(seen.knows(ALIAS, alias))  # type: ignore[attr-defined]

    async def test_who_knew_ken_keeps_what_they_knew_and_learns_nothing_of_the_guard(self) -> None:
        await self.join()
        seen = await self.view(self.sees_ken)
        self.assertTrue(seen.knows(ENTRY, self.ken))  # type: ignore[attr-defined]
        self.assertFalse(seen.knows(ALIAS, await self.alias_id()))  # type: ignore[attr-defined]

    async def test_the_dm_sees_both(self) -> None:
        await self.join()
        dm = await self.memory.visible_to(GUILD_A, self.c, Viewer.the_dm())
        self.assertTrue(dm.knows(ENTRY, self.ken))
        self.assertTrue(dm.knows(ALIAS, await self.alias_id()))

    async def test_undo_puts_both_sides_back(self) -> None:
        written = await self.join()
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        guard = await self.view(self.sees_guard)
        self.assertTrue(guard.knows(ENTRY, self.guard))  # type: ignore[attr-defined]
        ken = await self.view(self.sees_ken)
        self.assertTrue(ken.knows(ENTRY, self.ken))  # type: ignore[attr-defined]
        self.assertEqual(len(await self.memory.unnamed(GUILD_A, self.c)), 1)

    async def test_it_refuses_a_pair_that_is_not_unnamed_and_named(self) -> None:
        other = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        for a, b in ((self.ken, other), (self.ken, self.guard)):
            with self.subTest(a=a, b=b), self.assertRaises(MemoryRuleError):
                await self.memory.join_same_person(GUILD_A, self.c, a, b)
