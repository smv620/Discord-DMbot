"""Moving a campaign to the new owner's other server (#2532 slice C2): the new copy is made and
checked first, the old copy goes only afterwards and in the same change that holds its lock, any
failure leaves the old campaign whole and opens the offer again, and two sweeps move it once."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.moves import (
    CHANGED_HANDS,
    COPY_FAILED,
    SHARES_RULES,
    HandoverMover,
    Moved,
)
from tests.test_campaign_handover import BUYER, CO_DM, GUILD, NOW, OTHER_GUILD, OWNER, HandoverTest


class Moving(HandoverTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = NOW
        self.told: list[Moved] = []

        async def told(moved: Moved) -> None:
            self.told.append(moved)

        self.mover = HandoverMover(self.store, clock=lambda: self.now, told=told)
        self.offer_id = await self.ask_for_move()

    async def ask_for_move(self) -> int:
        """An offer by email, claimed by BUYER who asked for OTHER_GUILD (as the website does)."""
        offer = await self.store.offer_handover_by_email(
            GUILD, self.campaign.id, OWNER, NOW, from_name="Oskar", token_hash="a" * 64
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET to_user_id = %s, to_name = 'Bea',"
                " move_to_guild_id = %s WHERE guild_id = %s AND id = %s",
                (BUYER, OTHER_GUILD, GUILD, offer.id),
            )
        return offer.id

    async def row_of_offer(self) -> dict[str, Any] | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM campaign_handover_offers WHERE guild_id = %s AND id = %s",
                (GUILD, self.offer_id),
            )
            return await cur.fetchone()

    async def campaigns_in(self, guild: int) -> list[Any]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute("SELECT id, name, owner_user_id FROM campaigns")
            return list(await cur.fetchall())

    async def move(self) -> str:
        return await self.mover.move(GUILD, self.offer_id)

    async def test_the_campaign_moves_and_nothing_is_left_in_the_old_server(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        self.assertEqual(await self.mover.waiting(GUILD), [self.offer_id])
        self.assertEqual(await self.move(), "moved")
        [copy] = await self.campaigns_in(OTHER_GUILD)
        self.assertEqual((copy["name"], copy["owner_user_id"]), (self.campaign.name, BUYER))
        made = await self.store.get(OTHER_GUILD, copy["id"])
        assert made is not None
        self.assertIn(OWNER, made.dm_user_ids)  # the old owner stays a DM
        self.assertIn(BUYER, made.dm_user_ids)
        self.assertEqual(await self.campaigns_in(GUILD), [])  # the campaign is gone from here
        self.assertIsNone(await self.row_of_offer())  # and so is its offer
        async with self.db.guild(GUILD) as conn:
            for table in ("campaign_dms", "campaign_handover_offers"):
                cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
                self.assertEqual((await cur.fetchone() or {})["n"], 0, table)
        [told] = self.told
        self.assertEqual((told.result, told.new_campaign_id), ("moved", copy["id"]))

    async def test_a_failed_import_leaves_the_old_campaign_whole_and_opens_the_offer(self) -> None:
        with patch.object(
            CampaignStore, "import_backup", side_effect=CampaignError("No room in that plan.")
        ):
            self.assertEqual(await self.move(), "reopened")
        await self.assertWhole("No room in that plan.")

    async def test_a_crash_while_importing_leaves_the_old_campaign_whole(self) -> None:
        with patch.object(CampaignStore, "import_backup", side_effect=RuntimeError("boom")):
            self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(COPY_FAILED)

    async def test_a_copy_that_does_not_match_is_removed_and_the_old_one_stays(self) -> None:
        real = CampaignStore.export
        calls = 0

        async def lossy(store: CampaignStore, guild_id: int, campaign_id: str) -> dict[str, Any]:
            nonlocal calls
            data = await real(store, guild_id, campaign_id)
            calls += 1
            if calls == 2:  # the second export is of the copy: pretend it lost a section
                data["sections"] = {**data["sections"], "confirmations": [["x"]]}
            return data

        with patch.object(CampaignStore, "export", lossy):
            self.assertEqual(await self.move(), "reopened")
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])  # the bad copy is gone
        await self.assertWhole(COPY_FAILED)

    async def test_a_failure_removing_the_old_copy_loses_nothing_and_leaves_one_campaign(
        self,
    ) -> None:
        real = CampaignStore.delete_in

        async def fails_for_the_old_one(
            store: CampaignStore, conn: Any, guild_id: int, campaign_id: str
        ) -> bool:
            if guild_id == GUILD:
                raise RuntimeError("boom")
            return await real(store, conn, guild_id, campaign_id)

        with patch.object(CampaignStore, "delete_in", fails_for_the_old_one):
            self.assertEqual(await self.move(), "reopened")  # (the copy is taken back)
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])
        await self.assertWhole(COPY_FAILED)

    async def test_when_the_copy_cannot_be_taken_back_either_the_move_is_taken_up_again(
        self,
    ) -> None:
        real_delete = CampaignStore.delete

        async def cannot_delete_the_copy(
            store: CampaignStore, guild_id: int, campaign_id: str, **kw: Any
        ) -> None:
            if guild_id == OTHER_GUILD:
                raise RuntimeError("boom")
            await real_delete(store, guild_id, campaign_id, **kw)

        def old_one_fails(store: CampaignStore, conn: Any, guild_id: int, cid: str) -> Any:
            raise RuntimeError("boom")

        with (
            patch.object(CampaignStore, "delete_in", old_one_fails),
            patch.object(CampaignStore, "delete", cannot_delete_the_copy),
        ):
            self.assertEqual(await self.move(), "skipped")
        row = await self.row_of_offer()
        assert row is not None
        self.assertIsNotNone(row["moved_campaign_id"])  # the copy stays recorded
        self.assertEqual(len(await self.campaigns_in(GUILD)), 1)  # the old one is whole
        self.assertEqual(await self.move(), "moved")  # the next try finishes it
        self.assertEqual(len(await self.campaigns_in(OTHER_GUILD)), 1)  # still just one copy
        self.assertEqual(await self.campaigns_in(GUILD), [])

    async def test_a_move_cut_short_after_the_copy_is_taken_up_without_a_second_copy(self) -> None:
        # The process stopped after making and recording the copy but before removing the old one.
        data = await self.store.export(GUILD, self.campaign.id)
        copy = await self.store.import_backup(OTHER_GUILD, data, BUYER)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET moved_campaign_id = %s, move_claimed_at = %s"
                " WHERE guild_id = %s AND id = %s",
                (copy.id, NOW, GUILD, self.offer_id),
            )
        self.now = NOW + 60
        self.assertEqual(await self.move(), "skipped")  # the lease is still held
        self.now = NOW + 3600
        self.assertEqual(await self.move(), "moved")
        self.assertEqual([c["id"] for c in await self.campaigns_in(OTHER_GUILD)], [copy.id])
        self.assertEqual(await self.campaigns_in(GUILD), [])

    async def test_two_sweeps_at_once_move_it_once(self) -> None:
        results = await asyncio.gather(self.move(), self.move(), self.move())
        self.assertEqual(sorted(results), ["moved", "skipped", "skipped"])
        self.assertEqual(len(await self.campaigns_in(OTHER_GUILD)), 1)
        self.assertEqual(await self.campaigns_in(GUILD), [])

    async def test_a_campaign_that_changed_hands_is_not_moved(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE guild_id = %s", (CO_DM, GUILD)
            )
        self.assertEqual(await self.move(), "reopened")
        self.assertEqual(self.told[0].reason, CHANGED_HANDS)
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])

    async def test_a_campaign_that_shares_rules_is_not_moved_yet(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status,"
                " created_by, created_at) VALUES (%s, %s, %s, 'house_rules', 'Ours', 'shared',"
                " %s, %s)",
                (GUILD, self.campaign.id, "c" * 32, OWNER, NOW),
            )
        self.assertEqual(await self.move(), "reopened")
        self.assertEqual(self.told[0].reason, SHARES_RULES)
        self.assertEqual(len(await self.campaigns_in(GUILD)), 1)
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])

    async def test_an_offer_nobody_asked_to_move_is_left_alone(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE campaign_handover_offers SET move_to_guild_id = NULL")
        self.assertEqual(await self.mover.waiting(GUILD), [])
        self.assertEqual(await self.move(), "skipped")

    async def assertWhole(self, reason: str) -> None:
        """The old campaign is as it was, and the offer is open again with nobody's claim."""
        [still] = await self.campaigns_in(GUILD)
        self.assertEqual(still["owner_user_id"], OWNER)
        row = await self.row_of_offer()
        assert row is not None
        self.assertEqual(
            (row["status"], row["to_user_id"], row["move_to_guild_id"], row["moved_campaign_id"]),
            ("open", 0, None, None),
        )
        [told] = self.told
        self.assertEqual((told.result, told.reason), ("reopened", reason))
