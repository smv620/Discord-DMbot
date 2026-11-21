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
        # (the players' audience is the party's view, #4537 slice 5a: a party of one, made first,
        # so every entry shown to the table after it is written as known to it)
        await self.add(
            "Pcwyn Testhand", type="character", role="player_character", played_by=900_001
        )
        self.cerric = await self.add("Cerric", type="character", role="npc")
        self.far = await self.add("Far Keep", type="place")
        self.temple = await self.add("Old Temple", type="place")
        self.ulfgar = await self.add("Ulfgar", type="character", role="npc")
        self.veil = await self.add("Lord Veil", type="character", hidden_from_players=True)
        await self.memory.add_alias(
            GUILD_A,
            self.c,
            self.cerric,
            "the Grey Fox",
            kind="nickname",
            source="dm",
            secret=True,
            dm_chose_secret=True,
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

    # (#4911: the guard's names are now chosen in its query: each branch of it)
    async def test_an_open_other_name_of_a_hidden_entry_is_not_named(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.veil, "the Pale Lord", kind="nickname", source="dm",
            secret=False, dm_chose_secret=False, status="confirmed",
        )  # fmt: skip
        await self.link(self.cerric, "associated_with", self.temple, "fears the Pale Lord")
        self.assertNotIn("Pale", await self.says(PLAYERS))

    async def test_a_proposed_other_name_of_a_known_entry_is_not_named(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.ulfgar, "Stonebeard", kind="nickname", source="extractor",
            secret=False, dm_chose_secret=False, status="proposed",
        )  # fmt: skip
        await self.link(self.cerric, "associated_with", self.temple, "drinks with Stonebeard")
        self.assertNotIn("Stonebeard", await self.says(PLAYERS))

    async def test_a_dm_only_kind_is_not_named(self) -> None:
        await self.add("The Drowned Crown", type="quest")
        await self.link(self.cerric, "associated_with", self.temple, "seeks the Drowned Crown")
        self.assertNotIn("Drowned", await self.says(PLAYERS))
