"""The player-facing reader of what a player's characters know names each end as that player's
characters know it (#3037 ruling): after a join, "the guard at the gate", never "Ken". Invented
names."""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED
from tests.test_memory_store import GUILD_A
from tests.test_same_person_join import JoinBase


class TheReaderNamesEachEndAsTheCharacterKnowsIt(JoinBase):
    """`what_my_characters_know` is a player-facing reader: after a join it names the guard's
    connection through the secret other name, never "Ken" (Supervisor, #3037)."""

    async def hidden_connection(self, a: str) -> str:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        return str(made.value[0].id)

    async def test_a_joined_connection_reads_as_the_guard_and_never_ken(self) -> None:
        rel = await self.hidden_connection(self.guard)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.sees_guard], source="dm"
        )
        written = await self.join()
        after = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        (shown,) = after.connections
        self.assertEqual((shown.subject, shown.object), (self.label, "Captain Vale"))
        self.assertNotIn("Ken", repr(after))
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        undone = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertNotIn("Ken", repr(undone))  # (the unnamed person is not a named entry again)

    async def test_a_player_whose_character_knew_ken_is_shown_ken(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.ken, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        rel = str(made.value[0].id)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.sees_ken], source="dm")
        await self.join()
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 2)
        self.assertEqual([c.subject for c in mine.connections], ["Ken"])
