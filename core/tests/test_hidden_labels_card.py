"""The Discord name card for someone who may not see secrets (a server manager who is not a DM)
never carries a catch-all label that names an entry or a secret spelling they may not be told
(#3153); the DM's card keeps the words. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.ui import name_card
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

LABEL = "swore on Veil's grave to guard"


class OnTheCard(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.mira = await self.add("Mira")
        self.keep = await self.add("The Keep", type="place")
        self.veil = await self.add("Lord Veil", hidden_from_players=True)
        self.link = await self.memory.add_relation(
            GUILD_A, self.c, self.mira, "associated_with", self.keep,
            source="dm", confidence=1.0, status="confirmed", detail=LABEL,
        )  # fmt: skip

    async def card(self, secrets: bool) -> str:
        view = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        shown: Any = view.for_viewer(secrets)
        connections = await self.memory.relations(GUILD_A, self.c, entity_id=self.mira)
        text = name_card.card_text(shown, self.mira, connections, secrets=secrets)
        assert text is not None
        return text

    async def test_a_label_naming_a_hidden_entry_is_blanked_for_a_manager(self) -> None:
        text = await self.card(False)
        self.assertNotIn("Veil", text)
        self.assertNotIn("grave", text)
        self.assertIn("The Keep", text)  # (the connection itself is still shown)

    async def test_the_dm_card_keeps_the_words(self) -> None:
        self.assertIn("grave", await self.card(True))

    async def test_a_label_naming_a_secret_spelling_is_blanked_too(self) -> None:
        other = await self.add("Kesh")
        await self.memory.add_alias(
            GUILD_A, self.c, other, "the Grey Fox", kind="nickname", source="dm", secret=True
        )
        await self.memory.add_relation(
            GUILD_A, self.c, self.mira, "associated_with", other,
            source="dm", confidence=1.0, status="confirmed", detail="owes the Grey Fox a debt",
        )  # fmt: skip
        text = await self.card(False)
        self.assertNotIn("Grey Fox", text)
        self.assertIn("Kesh", text)

    async def test_a_label_naming_only_known_entries_is_kept(self) -> None:
        other = await self.add("Kesh")
        await self.memory.add_relation(
            GUILD_A, self.c, self.mira, "associated_with", other,
            source="dm", confidence=1.0, status="confirmed", detail="taught by Kesh",
        )  # fmt: skip
        self.assertIn("taught by Kesh", await self.card(False))
