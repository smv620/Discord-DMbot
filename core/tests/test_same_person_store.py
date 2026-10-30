"""The database side of #3037's offers: one campaign's rows in, the offers out; a No remembered;
nothing read across campaigns. Invented names."""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED
from dmbot.memory.same_person import WORDS
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest


class TheOffers(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="guard at the north gate", role="guard"
        )
        self.guard = made.entity.id
        self.ken = await self.add(
            "Ken",
            type="character",
            status=CONFIRMED,
            description="A guard who keeps the north gate",
        )
        self.mira = await self.add("Mira", type="character", status=CONFIRMED, description="Baker")

    async def test_the_unnamed_guard_is_offered_the_named_one_with_the_evidence(self) -> None:
        (offer,) = await self.memory.same_person_offers(GUILD_A, self.c)
        self.assertEqual((offer.unnamed_id, offer.named_id), (self.guard, self.ken))
        self.assertEqual(offer.reasons, (WORDS,))

    async def test_a_no_is_remembered_and_the_pair_is_not_offered_again(self) -> None:
        await self.memory.not_the_same_person(GUILD_A, self.c, self.guard, self.ken, user_id=7)
        self.assertEqual(await self.memory.same_person_offers(GUILD_A, self.c), [])

    async def test_a_no_for_one_name_leaves_other_names(self) -> None:
        other = await self.add(
            "Bram", type="character", status=CONFIRMED, description="Guard at the north gate"
        )
        await self.memory.not_the_same_person(GUILD_A, self.c, self.guard, self.ken, user_id=7)
        (offer,) = await self.memory.same_person_offers(GUILD_A, self.c)
        self.assertEqual(offer.named_id, other)

    async def test_a_secret_other_name_counts_as_evidence_for_the_dm_only_reader(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Gate Guard Mira", kind="nickname", secret=True,
            source="dm", status=CONFIRMED,
        )  # fmt: skip
        found = {o.named_id for o in await self.memory.same_person_offers(GUILD_A, self.c)}
        self.assertEqual(found, {self.ken, self.mira})

    async def test_nothing_is_read_from_another_campaign(self) -> None:
        c2 = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        other = await self.memory.add_unnamed(
            GUILD_A, c2, what="guard at the north gate", role="guard"
        )
        self.assertTrue(other.entity.id)
        offers = await self.memory.same_person_offers(GUILD_A, c2)
        self.assertEqual(offers, [])  # (Ken lives in the other campaign)
