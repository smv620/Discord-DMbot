"""A waiting name that is a list is split by the DM, and the split can be undone (#3383)."""

from __future__ import annotations

from dmbot.memory.models import PROPOSED, REJECTED, MemoryRuleError, TooLateToUndo
from tests.test_memory_store import GUILD_A, MemoryTest


class SplitWaitingName(MemoryTest):
    async def waiting(self, name: str) -> str:
        return await self.add(name, type="place", status=PROPOSED)

    async def names(self, *statuses: str) -> list[str]:
        data = await self.memory.lookup_data(GUILD_A, self.c)
        return sorted(e.name for e in data.entities if e.status in statuses)

    async def test_a_list_becomes_a_waiting_name_for_each_part(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        written = await self.memory.split_waiting_name(GUILD_A, self.c, listed, source="dm")
        self.assertEqual(written.value.parts, ("Aldmoor", "Quillgate", "Fenwick"))
        self.assertEqual(await self.names(PROPOSED), ["Aldmoor", "Fenwick", "Quillgate"])

    async def test_a_part_already_known_is_joined_not_asked(self) -> None:
        await self.add("Quillgate", type="place", status="confirmed")
        listed = await self.waiting("Aldmoor, Quillgate")
        written = await self.memory.split_waiting_name(GUILD_A, self.c, listed, source="dm")
        self.assertEqual((written.value.made, written.value.joined), (("Aldmoor",), ("Quillgate",)))
        self.assertEqual(await self.names(PROPOSED), ["Aldmoor"])

    async def test_undo_puts_the_one_question_back(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        before = await self.snapshot()
        written = await self.memory.split_waiting_name(GUILD_A, self.c, listed, source="dm")
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(await self.names(PROPOSED), ["Aldmoor, Quillgate, Fenwick"])

    async def test_undo_is_refused_once_a_part_is_answered(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate")
        written = await self.memory.split_waiting_name(GUILD_A, self.c, listed, source="dm")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        part = next(e for e in data.entities if e.name == "Aldmoor")
        await self.memory.set_entity_status(GUILD_A, self.c, part.id, REJECTED, source="dm")
        assert written.batch is not None
        with self.assertRaises((MemoryRuleError, TooLateToUndo)):
            await self.memory.undo(GUILD_A, self.c, written.batch)

    async def test_one_name_is_not_split_and_only_the_dm_can(self) -> None:
        single = await self.waiting("Salt and Iron Company")
        with self.assertRaises(MemoryRuleError):
            await self.memory.split_waiting_name(GUILD_A, self.c, single, source="dm")
        listed = await self.waiting("Aldmoor, Quillgate")
        with self.assertRaises(MemoryRuleError):
            await self.memory.split_waiting_name(GUILD_A, self.c, listed, source="extractor")
