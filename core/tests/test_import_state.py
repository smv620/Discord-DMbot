"""What an import's scans leave for its report, in the database (#4904 step 1): counters and sets
that add up across calls, are taken once, never cross servers or campaigns, and go with the
campaign. Database tests: skipped where there is no Postgres."""

from __future__ import annotations

import asyncio

from dmbot.campaigns.store import CampaignStore
from dmbot.import_state import ImportState
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        campaigns = CampaignStore(self.db)
        self.one = await campaigns.create(1, "One", fx.DM)
        self.two = await campaigns.create(1, "Two", fx.DM)
        self.other = await campaigns.create(2, "Other", fx.DM)
        self.state = ImportState(self.db, clock=lambda: 1_700_000_000.0)


class TheCounters(Base):
    async def test_a_counter_adds_up_and_is_taken_once(self) -> None:
        self.assertEqual(await self.state.bump(1, self.one.id, "unchecked"), 1)
        self.assertEqual(await self.state.bump(1, self.one.id, "unchecked"), 2)
        self.assertEqual(await self.state.count(1, self.one.id, "unchecked"), 2)
        self.assertEqual(await self.state.take_count(1, self.one.id, "unchecked"), 2)
        self.assertEqual(await self.state.take_count(1, self.one.id, "unchecked"), 0)

    async def test_put_count_sets_it_and_zero_removes_it(self) -> None:
        await self.state.put_count(1, self.one.id, "left_told", 3)
        self.assertEqual(await self.state.counted(1, "left_told"), {self.one.id: 3})
        await self.state.put_count(1, self.one.id, "left_told", 0)
        self.assertEqual(await self.state.counted(1, "left_told"), {})

    async def test_put_count_overwrites_and_adds_to_nothing(self) -> None:
        await self.state.put_count(1, self.one.id, "left_told", 3)
        await self.state.put_count(1, self.one.id, "left_told", 1)
        self.assertEqual(await self.state.count(1, self.one.id, "left_told"), 1)

    async def test_raise_count_only_ever_raises(self) -> None:
        await self.state.raise_count(1, self.one.id, "party", 3)
        await self.state.raise_count(
            1, self.one.id, "party", 2
        )  # (a smaller one does not lower it)
        self.assertEqual(await self.state.count(1, self.one.id, "party"), 3)
        await self.state.raise_count(1, self.one.id, "party", 5)
        self.assertEqual(await self.state.count(1, self.one.id, "party"), 5)

    async def test_campaigns_and_servers_are_apart(self) -> None:
        await self.state.bump(1, self.one.id, "unchecked")
        await self.state.bump(2, self.other.id, "unchecked", 5)
        self.assertEqual(await self.state.count(1, self.two.id, "unchecked"), 0)
        self.assertEqual(await self.state.counted(1, "unchecked"), {self.one.id: 1})
        self.assertEqual(await self.state.counted(2, "unchecked"), {self.other.id: 5})

    async def test_two_bumps_at_once_lose_nothing(self) -> None:
        await asyncio.gather(*(self.state.bump(1, self.one.id, "unchecked") for _ in range(8)))
        self.assertEqual(await self.state.count(1, self.one.id, "unchecked"), 8)

    async def test_a_kind_must_be_letters_and_underscores(self) -> None:
        with self.assertRaises(ValueError):
            await self.state.bump(1, self.one.id, "Not A Kind")


class TheSets(Base):
    async def test_strings_add_once_in_the_order_first_added_and_are_taken_once(self) -> None:
        await self.state.add_strings(1, self.one.id, "names", ["Bryn", "Kazeth"])
        await self.state.add_strings(1, self.one.id, "names", ["Kazeth", "Ember"])
        self.assertEqual(
            await self.state.take_strings(1, self.one.id, "names"), ["Bryn", "Kazeth", "Ember"]
        )
        self.assertEqual(await self.state.take_strings(1, self.one.id, "names"), [])

    async def test_add_each_keeps_alike_values_one_by_one_in_order_and_takes_them_once(
        self,
    ) -> None:
        await self.state.add_each(1, self.one.id, "unhonoured", ["switched off", "plan"])
        await self.state.add_each(1, self.one.id, "unhonoured", ["switched off"])
        got = await self.state.take_each(1, self.one.id, "unhonoured")
        self.assertEqual(got, ["switched off", "plan", "switched off"])
        self.assertEqual(await self.state.take_each(1, self.one.id, "unhonoured"), [])

    async def test_take_each_starting_takes_only_that_prefix_in_one_statement(self) -> None:
        await self.state.add_each(
            1, self.one.id, "name_asks", ["s1 3 Keirandal", "s2 2 Fenwick", "s1 1 Vane"]
        )
        got = await self.state.take_each_starting(1, self.one.id, "name_asks", "s1 ")
        self.assertEqual(got, ["s1 3 Keirandal", "s1 1 Vane"])
        self.assertEqual(
            await self.state.take_each_starting(1, self.one.id, "name_asks", "s1 "), []
        )
        self.assertEqual(
            await self.state.take_each_starting(1, self.one.id, "name_asks", "s2 "),
            ["s2 2 Fenwick"],
        )
        self.assertEqual(await self.state.take_each_starting(1, self.one.id, "nothing", "x"), [])

    async def test_a_value_with_a_colon_comes_back_whole(self) -> None:
        await self.state.add_each(1, self.one.id, "unhonoured", ["it said: no"])
        self.assertEqual(await self.state.take_each(1, self.one.id, "unhonoured"), ["it said: no"])

    async def test_nothing_added_leaves_no_row(self) -> None:
        await self.state.add_strings(1, self.one.id, "names", [])
        self.assertEqual(await self.state.campaigns_with(1, ["names", "unchecked"]), set())

    async def test_campaigns_with_finds_any_of_the_kinds(self) -> None:
        await self.state.add_strings(1, self.one.id, "names", ["Bryn"])
        await self.state.bump(1, self.two.id, "unchecked")
        self.assertEqual(
            await self.state.campaigns_with(1, ["names", "unchecked"]), {self.one.id, self.two.id}
        )
        self.assertEqual(await self.state.campaigns_with(2, ["names", "unchecked"]), set())

    async def test_it_goes_with_the_campaign(self) -> None:
        await self.state.bump(1, self.two.id, "unchecked")
        await CampaignStore(self.db).delete(1, self.two.id)
        self.assertEqual(await self.state.counted(1, "unchecked"), {})
