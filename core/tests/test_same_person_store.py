"""The database side of #3037's offers: one campaign's rows in, the offers out; a No remembered;
nothing read across campaigns. Invented names."""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.same_person import CONNECTIONS, WORDS
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

    async def test_a_no_for_a_pair_that_is_not_unnamed_and_named_is_refused(self) -> None:
        c2 = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        far = await self.add("Far", campaign=c2, type="character", status=CONFIRMED)
        for a, b in (
            (self.ken, self.mira),  # two named
            (self.guard, far),  # another campaign
            (self.ken, self.guard),  # the wrong way round
        ):
            with self.subTest(a=a, b=b), self.assertRaises(MemoryRuleError):
                await self.memory.not_the_same_person(GUILD_A, self.c, a, b, user_id=7)

    async def test_saying_no_twice_is_fine(self) -> None:
        for _ in range(2):
            await self.memory.not_the_same_person(GUILD_A, self.c, self.guard, self.ken, user_id=7)
        self.assertEqual(await self.memory.same_person_offers(GUILD_A, self.c), [])

    async def test_a_proposed_or_merged_named_entry_is_never_offered(self) -> None:
        await self.add(
            "Pip", type="character", status="proposed", description="Guard of the north gate"
        )
        twin = await self.add(
            "Dov", type="character", status=CONFIRMED, description="Guard of the north gate"
        )
        await self.memory.merge(GUILD_A, self.c, self.ken, twin, source="dm", dm_said_same=True)
        found = {o.named_id for o in await self.memory.same_person_offers(GUILD_A, self.c)}
        self.assertEqual(found, {self.ken})  # (Pip waits; Dov is now Ken)

    async def test_shared_connections_are_evidence(self) -> None:
        captain = await self.add("Captain Vale", type="character", status=CONFIRMED)
        town = await self.add("Greywater", type="place", status=CONFIRMED)
        for who in (self.guard, self.mira):
            for pred, other in (("knows", captain), ("located_in", town)):
                await self.relate(who, pred, other, status=CONFIRMED)
        found = {
            o.named_id: o.reasons for o in await self.memory.same_person_offers(GUILD_A, self.c)
        }
        self.assertIn(CONNECTIONS, found[self.mira])

    async def test_a_proposed_other_name_is_not_evidence(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Gate Guard Mira", kind="nickname", source="extractor"
        )
        found = {o.named_id for o in await self.memory.same_person_offers(GUILD_A, self.c)}
        self.assertNotIn(self.mira, found)

    async def test_another_campaigns_names_connections_and_noes_are_never_read(self) -> None:
        c2 = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        far_guard = (
            await self.memory.add_unnamed(GUILD_A, c2, what="guard at the north gate", role="guard")
        ).entity.id
        far_ken = await self.add(
            "Far Ken", campaign=c2, type="character", status=CONFIRMED,
            description="A guard who keeps the north gate",
        )  # fmt: skip
        await self.memory.not_the_same_person(GUILD_A, c2, far_guard, far_ken, user_id=7)
        # the other campaign's No hides nothing here
        (offer,) = await self.memory.same_person_offers(GUILD_A, self.c)
        self.assertEqual((offer.unnamed_id, offer.named_id), (self.guard, self.ken))
        self.assertEqual(await self.memory.same_person_offers(GUILD_A, c2), [])
