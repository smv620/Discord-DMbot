"""Who was in which split-party group (#3131, migration 0310): the DM places kept player characters,
the history closes at a rejoin and comes back with its Undo, and a line's speaker reads as the group
they were in when it was spoken. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.memory.store import MemoryStore
from dmbot.timebot import split
from dmbot.timebot.clock import Clock
from dmbot.timebot.stamp import game_time_at, group_at, group_of_speakers
from dmbot.timebot.store import (
    GONE,
    NOT_A_DM,
    NOT_A_PLAYER_CHARACTER,
    ClockSection,
    ClockStore,
)
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, ANNA, BEN, OTHER = 7, 101, 102, 103


class GroupCase(DatabaseTest):
    """A campaign with a clock, a split party of two groups and three player characters."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.clocks = ClockStore(self.db)
        self.memory = MemoryStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Alpha", DM)
        self.b = await self.campaigns.create(GUILD_B, "Beta", DM)
        self.vell = await self.character("Vell", ANNA)
        self.tuck = await self.character("Tuck", BEN)
        self.mara = await self.character("Mara", ANNA)  # (Anna plays two)
        self.guard = (
            await self.memory.add_entity(
                GUILD_A, self.a.id, name="Guard", type="npc", status="confirmed", source="dm"
            )
        ).value.id

    async def character(self, name: str, player: int, campaign: Any = None) -> str:
        campaign = campaign or self.a
        made = await self.memory.add_entity(
            campaign.guild_id if hasattr(campaign, "guild_id") else GUILD_A,
            campaign.id,
            name=name,
            type="character",
            role="player_character",
            played_by=player,
            status="confirmed",
            source="dm",
        )
        return str(made.value.id)

    async def split(self) -> str:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(2000, 0))
        _, token = await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        return token

    async def rows(self) -> list[tuple[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT character_id, position FROM game_clock_group_log WHERE guild_id = %s"
                " AND campaign_id = %s ORDER BY seq",
                (GUILD_A, self.a.id),
            )
            return [(str(r["character_id"]), r["position"]) for r in await cur.fetchall()]

    async def place(self, **who: int | None) -> dict[str, int]:
        ids = {"vell": self.vell, "tuck": self.tuck, "mara": self.mara}
        return await self.clocks.place_members(
            GUILD_A, self.a.id, DM, {ids[name]: group for name, group in who.items()}
        )


class Placing(GroupCase):
    async def test_the_dm_places_characters_and_only_a_change_writes_a_row(self) -> None:
        await self.split()
        self.assertEqual(await self.place(vell=1, tuck=2), {self.vell: 1, self.tuck: 2})
        await self.place(vell=1, tuck=1)  # vell is where they were: no new row for them
        rows = await self.rows()
        self.assertEqual(sorted(rows[:2]), sorted([(self.vell, 1), (self.tuck, 2)]))
        self.assertEqual(rows[2:], [(self.tuck, 1)])
        self.assertEqual(
            await self.clocks.members(GUILD_A, self.a.id), {self.vell: 1, self.tuck: 1}
        )
        await self.place(vell=None)  # taken out of any group
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {self.tuck: 1})

    async def test_only_this_campaigns_player_characters_in_groups_that_exist(self) -> None:
        await self.split()
        theirs = await self.character("Other", OTHER, self.b)
        for bad in ({self.guard: 1}, {theirs: 1}, {"no-such-id": 1}, {self.vell: 1, self.guard: 2}):
            with self.subTest(str(bad)[:40]), self.assertRaises(CampaignError) as why:
                await self.clocks.place_members(GUILD_A, self.a.id, DM, bad)
            self.assertEqual(str(why.exception), NOT_A_PLAYER_CHARACTER)
        with self.assertRaises(CampaignError) as gone:
            await self.place(vell=3)  # (the party has two groups)
        self.assertEqual(str(gone.exception), GONE)
        self.assertEqual(
            await self.rows(), []
        )  # (a refusal writes nothing, not even the good part)

    async def test_only_a_dm_and_only_while_split(self) -> None:
        with self.assertRaises(CampaignError) as together:
            await self.place(vell=1)
        self.assertEqual(str(together.exception), split.NOT_SPLIT)
        await self.split()
        with self.assertRaises(CampaignError) as not_dm:
            await self.clocks.place_members(GUILD_A, self.a.id, BEN, {self.vell: 1})
        self.assertEqual(str(not_dm.exception), NOT_A_DM)
        self.assertEqual(await self.rows(), [])


class TheHistory(GroupCase):
    async def test_a_rejoin_closes_the_groups_and_its_undo_brings_them_back(self) -> None:
        await self.split()
        await self.place(vell=1, tuck=2)
        rejoined = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {})
        self.assertEqual(
            sorted((await self.rows())[-2:]), sorted([(self.vell, None), (self.tuck, None)])
        )
        self.assertTrue(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, rejoined.token))
        self.assertEqual(
            await self.clocks.members(GUILD_A, self.a.id), {self.vell: 1, self.tuck: 2}
        )

    async def test_undoing_a_split_closes_whoever_was_placed(self) -> None:
        token = await self.split()
        await self.place(vell=1, tuck=2)
        self.assertTrue(await self.clocks.undo_split(GUILD_A, self.a.id, DM, token))
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {})

    async def test_a_later_split_places_the_same_character_elsewhere_and_each_period_keeps_its_own(
        self,
    ) -> None:
        await self.split()
        await self.place(vell=1)
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        await self.clocks.split(GUILD_A, self.a.id, DM, ["North", "South"])
        await self.place(vell=2)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE game_clock_group_log SET at = 100 + seq WHERE guild_id = %s",
                (GUILD_A,),
            )
            cur = await conn.execute(
                "SELECT seq, position FROM game_clock_group_log WHERE guild_id = %s"
                " AND character_id = %s ORDER BY seq",
                (GUILD_A, self.vell),
            )
            seqs = [(int(r["seq"]), r["position"]) for r in await cur.fetchall()]
            self.assertEqual([p for _, p in seqs], [1, None, 2])
            first, closed, second = (100 + s for s, _ in seqs)
            self.assertEqual(await group_at(conn, GUILD_A, self.a.id, self.vell, first), 1)
            self.assertIsNone(await group_at(conn, GUILD_A, self.a.id, self.vell, closed))
            self.assertEqual(await group_at(conn, GUILD_A, self.a.id, self.vell, second), 2)
            self.assertIsNone(await group_at(conn, GUILD_A, self.a.id, self.vell, 99))

    async def test_an_erased_character_takes_its_rows(self) -> None:
        await self.split()
        await self.place(vell=1, tuck=2)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_entities WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.a.id, self.vell),
            )
        self.assertEqual(await self.rows(), [(self.tuck, 2)])

    async def test_a_late_undo_skips_a_character_that_is_gone(self) -> None:
        await self.split()
        await self.place(vell=1, tuck=2)
        rejoined = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_entities WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.a.id, self.vell),
            )
        self.assertTrue(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, rejoined.token))
        self.assertEqual(await self.clocks.members(GUILD_A, self.a.id), {self.tuck: 2})

    async def test_another_campaigns_history_is_never_read(self) -> None:
        await self.split()
        await self.place(vell=1)
        async with self.db.guild(GUILD_B) as conn:
            self.assertIsNone(await group_at(conn, GUILD_B, self.b.id, self.vell, 10**9))
            self.assertIsNone(await group_at(conn, GUILD_B, self.a.id, self.vell, 10**9))


class TheSpeakersGroup(GroupCase):
    async def moments(self) -> None:
        """Real times 100.. for the rows, so a line can be put between them."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE game_clock_group_log SET at = 100 WHERE guild_id = %s", (GUILD_A,)
            )
            await conn.execute("UPDATE game_clock_log SET at = 50 WHERE guild_id = %s", (GUILD_A,))

    async def group(self, *users: int, at: int = 150) -> int | None:
        async with self.db.guild(GUILD_A) as conn:
            return await group_of_speakers(conn, GUILD_A, self.a.id, users, at)

    async def test_a_speaker_with_one_character_is_in_that_characters_group(self) -> None:
        await self.split()
        await self.place(tuck=2)
        await self.moments()
        self.assertEqual(await self.group(BEN), 2)

    async def test_not_known_when_unplaced_or_split_across_groups_or_no_character(self) -> None:
        await self.split()
        await self.place(vell=1, tuck=2)  # (Anna's other character, Mara, is not placed)
        await self.moments()
        self.assertIsNone(await self.group(ANNA))  # Mara has no group: whose voice was it?
        await self.place(mara=2)
        await self.moments()
        self.assertIsNone(await self.group(ANNA))  # Vell in 1, Mara in 2
        await self.place(mara=1)
        await self.moments()
        self.assertEqual(await self.group(ANNA), 1)  # both in 1: unambiguous
        self.assertIsNone(await self.group(ANNA, BEN))  # two speakers in different groups
        self.assertIsNone(await self.group(OTHER))  # plays no character here
        self.assertIsNone(await self.group())  # a line with no known speaker

    async def test_the_clock_of_the_speakers_group_is_the_lines_game_time(self) -> None:
        await self.split()
        await self.place(vell=1, mara=1, tuck=2)
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(2090, 0))  # group 1
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE game_clock_group_log SET at = 100 WHERE guild_id = %s", (GUILD_A,)
            )
            await conn.execute("UPDATE game_clock_log SET at = 200 WHERE guild_id = %s", (GUILD_A,))
            anna = await group_of_speakers(conn, GUILD_A, self.a.id, [ANNA], 300)
            ben = await group_of_speakers(conn, GUILD_A, self.a.id, [BEN], 300)
            self.assertEqual((anna, ben), (1, 2))
            self.assertEqual(await game_time_at(conn, GUILD_A, self.a.id, 300, anna), 2090)
            self.assertEqual(await game_time_at(conn, GUILD_A, self.a.id, 300, ben), 2000)
            self.assertIsNone(await game_time_at(conn, GUILD_A, self.a.id, 300, None))
