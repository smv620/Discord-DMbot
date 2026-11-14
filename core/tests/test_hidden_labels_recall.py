"""What the players' recall says about a catch-all connection never carries words that name an
entry or a secret spelling the players may not be told (#3153). The DM screen keeps the words.
Invented names."""

from __future__ import annotations

from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

LABEL = "swore on Veil's grave to guard"


class OnTheRecall(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cerric = await self.add("Cerric", type="character", role="npc")
        self.far = await self.add("Far Keep", type="place")
        self.temple = await self.add("Old Temple", type="place")
        self.ulfgar = await self.add("Ulfgar", type="character", role="npc")
        self.veil = await self.add("Lord Veil", type="character", hidden_from_players=True)
        await self.memory.add_alias(
            GUILD_A, self.c, self.cerric, "the Grey Fox", kind="nickname", source="dm", secret=True
        )
        await self.link(self.cerric, "associated_with", self.far, detail=LABEL)

    async def says(self, audience: str) -> str:
        got = await RecallService(self.memory, None).recall(
            GUILD_A, self.c, seeds=["Cerric"], audience=audience, opted_in=()
        )
        return " | ".join(i.text for i in got.items)

    async def link(self, a: str, how: str, b: str, detail: str) -> None:
        await self.memory.add_relation(
            GUILD_A, self.c, a, how, b, source="dm", confidence=1.0, status="confirmed",
            detail=detail,
        )  # fmt: skip

    async def test_players_do_not_hear_a_hidden_entry_in_the_words(self) -> None:
        text = await self.says(PLAYERS)
        self.assertNotIn("Veil", text)
        self.assertNotIn("grave", text)
        self.assertIn("Far Keep", text)  # (the connection itself is still told)

    async def test_the_dm_screen_keeps_the_words(self) -> None:
        self.assertIn("grave", await self.says(DM_SCREEN))

    async def test_players_do_not_hear_a_secret_spelling_in_the_words(self) -> None:
        await self.link(self.cerric, "associated_with", self.temple, "owes the Grey Fox a debt")
        text = await self.says(PLAYERS)
        self.assertNotIn("Grey Fox", text)

    async def test_words_naming_only_what_players_know_are_kept(self) -> None:
        await self.link(self.cerric, "associated_with", self.temple, "taught by Ulfgar")
        self.assertIn("taught by Ulfgar", await self.says(PLAYERS))

    async def test_a_proposed_entry_the_dm_has_not_confirmed_is_not_named(self) -> None:
        await self.add("Sir Dunmoor", type="character", status="proposed")
        await self.link(self.cerric, "associated_with", self.temple, "bound by oath to Sir Dunmoor")
        self.assertNotIn("Dunmoor", await self.says(PLAYERS))
