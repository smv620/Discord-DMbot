"""An entry named by a description ("the woman") is offered to the DM to become an unnamed person
(#3431). Real Postgres; invented names."""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_memory_store import GUILD_A, MemoryTest


class TheDescribedEntries(MemoryTest):
    async def made(self, name: str, **kw: object) -> str:
        kw.setdefault("source", "extractor")
        kw.setdefault("type", "npc")
        return await self.add(name, **kw)  # type: ignore[arg-type]

    async def test_only_a_description_made_by_dmbot_is_offered(self) -> None:
        woman = await self.made("the woman")
        await self.made("Mira Vane")
        await self.made("the ferryman", source="dm")
        found = await self.memory.described_entries(GUILD_A, self.c)
        self.assertEqual([e.id for e in found], [woman])

    async def test_an_entry_with_a_real_other_name_is_not_offered(self) -> None:
        woman = await self.made("the woman")
        await self.memory.add_alias(
            GUILD_A, self.c, woman, "Ysolde", kind="full", source="dm", status=CONFIRMED
        )
        self.assertEqual(await self.memory.described_entries(GUILD_A, self.c), [])

    async def test_the_dm_makes_it_an_unnamed_person_and_undo_takes_it_back(self) -> None:
        woman = await self.made("the woman")
        done = await self.memory.make_unnamed(GUILD_A, self.c, woman, source="dm")
        self.assertTrue(done.value.unnamed)
        self.assertEqual(done.value.unnamed_what, "the woman")
        self.assertEqual(done.value.name, "the woman (name unknown)")
        self.assertEqual(await self.memory.described_entries(GUILD_A, self.c), [])
        listed = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual([e.id for e, _ in listed], [woman])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        again = await self.memory.described_entries(GUILD_A, self.c)
        self.assertEqual([e.id for e in again], [woman])

    async def test_a_named_entry_or_a_non_dm_source_is_refused(self) -> None:
        mira = await self.made("Mira Vane")
        woman = await self.made("the woman")
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_unnamed(GUILD_A, self.c, mira, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_unnamed(GUILD_A, self.c, woman, source="extractor")
