"""The story threads a recap's writer may see (#2995, slice 3): asked of the one gate, as "what
every player character knows", never of the old flags. A thread only the DM, an NPC or some of the
player characters know is the DM's own plot. Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.memory.gate import ENTRY
from dmbot.memory.models import GOING, HINTED, OPEN, RESOLVED
from dmbot.recaps.discord import RecapFeatures
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest


class TheOpenThreads(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ada = await self.pc("Ada", 1)
        self.bryn = await self.pc("Bryn", 2)
        self.npc = await self.add("Old Tam", type="character", role="npc")

    async def pc(self, name: str, user: int) -> str:
        return await self.add(
            name, type="character", role="player_character", played_by=user, status="confirmed"
        )

    async def thread(self, name: str, knowers: list[str], state: str = OPEN) -> str:
        made = await self.memory.new_thread(GUILD_A, self.c, name, "quest", state, source="dm")
        thread_id = made.value.id
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, ENTRY, thread_id, knowers, source="dm"
        )
        return str(thread_id)

    async def seen(self) -> tuple[str, ...]:
        me = SimpleNamespace(memory=self.memory)
        return await RecapFeatures._open_threads(cast(Any, me), GUILD_A, self.c)

    async def test_only_a_thread_every_player_character_knows_goes_to_the_writer(self) -> None:
        await self.thread("The Missing Heir", [self.ada, self.bryn])
        await self.thread("The Secret Pact", [])  # only the DM
        await self.thread("The Poisoned Well", [self.ada])  # only some of the party
        await self.thread("Tam's Debt", [self.npc])  # an NPC alone
        self.assertEqual(await self.seen(), ("The Missing Heir",))

    async def test_the_list_decides_not_the_old_flag(self) -> None:
        shown = await self.thread("The Missing Heir", [self.ada, self.bryn])
        hidden = await self.thread("The Secret Pact", [])
        async with self.db.guild(GUILD_A) as conn:  # the flags say the opposite of the lists
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = TRUE WHERE id = %s", (shown,)
            )
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = FALSE,"
                " audience_limited = FALSE WHERE id = %s",
                (hidden,),
            )
        self.assertEqual(await self.seen(), ("The Missing Heir",))

    async def test_only_threads_still_open_go(self) -> None:
        await self.thread("Hinted", [self.ada, self.bryn], HINTED)
        await self.thread("Going", [self.ada, self.bryn], GOING)
        await self.thread("Done", [self.ada, self.bryn], RESOLVED)
        self.assertCountEqual(await self.seen(), ("Hinted", "Going"))

    async def test_a_campaign_with_no_player_characters_sends_none(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Empty", 7)
        made = await self.memory.new_thread(GUILD_A, other.id, "Lonely", "quest", OPEN, source="dm")
        self.assertIsNotNone(made.value.id)
        me = SimpleNamespace(memory=self.memory)
        found = await RecapFeatures._open_threads(cast(Any, me), GUILD_A, other.id)
        self.assertEqual(found, ())

    async def test_another_campaigns_party_does_not_open_a_thread(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Other", 7)
        made = await self.memory.new_thread(GUILD_A, other.id, "Theirs", "quest", OPEN, source="dm")
        await self.memory.set_statement_knowers(
            GUILD_A, other.id, ENTRY, made.value.id, [], source="dm"
        )
        await self.thread("Mine", [self.ada, self.bryn])
        self.assertEqual(await self.seen(), ("Mine",))

    async def test_a_failed_question_sends_none_and_never_raises(self) -> None:
        self.memory.visible_to = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.thread("The Missing Heir", [self.ada, self.bryn])
        with self.assertLogs("dmbot.bot", level="ERROR"):  # (this module logs as the bot)
            self.assertEqual(await self.seen(), ())
