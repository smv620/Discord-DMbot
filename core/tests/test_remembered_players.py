"""A DM's remembered players in Discord's player pickers (#4724, the Discord half of #4226): the
DM's own list first, marked, only people in the server now, never another DM's, nothing at all with
the switch off. Stand-ins for Discord; the reader of the list runs against a real database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.memory.store import MemoryStore
from dmbot.ui import remembered_players as rp
from tests.pg import DatabaseTest

DM, OTHER_DM, ALICE, BOB, CAL, BOT = 7001, 7002, 8001, 8002, 8003, 8099


def member(user_id: int, name: str, *, bot: bool = False) -> Any:
    return SimpleNamespace(id=user_id, display_name=name, bot=bot)


def guild(*members: Any) -> Any:
    by_id = {m.id: m for m in members}
    return SimpleNamespace(get_member=lambda user_id: by_id.get(user_id))


class TheMembers(unittest.TestCase):
    def test_only_people_in_the_server_now_in_the_lists_order_never_a_bot_or_the_dm(self) -> None:
        here = guild(member(BOB, "Bob"), member(ALICE, "Alice"), member(BOT, "Bot", bot=True),
                     member(DM, "The DM"))  # fmt: skip
        got = rp.members_of(here, [ALICE, 1234, BOT, DM, BOB], DM)
        self.assertEqual([m.id for m in got], [ALICE, BOB])  # (1234 has left; no bot, no DM)

    def test_at_most_one_menus_worth(self) -> None:
        many = [member(9000 + i, f"P{i}") for i in range(40)]
        got = rp.members_of(guild(*many), [m.id for m in many], DM)
        self.assertEqual(len(got), rp.MAX_OPTIONS)


class TheMenu(unittest.IsolatedAsyncioTestCase):
    def interaction(self, *, on: bool, here: Any, ids: list[int]) -> Any:
        client = SimpleNamespace(
            settings=SimpleNamespace(remembered_players_on=on),
            get_guild=lambda guild_id: here,
            campaigns=SimpleNamespace(db=object()),
        )
        return SimpleNamespace(client=client, user=SimpleNamespace(id=DM))

    async def run_add(self, it: Any, ids: list[int], view: discord.ui.View) -> None:
        original = rp.remembered_ids
        rp.remembered_ids = AsyncMock(return_value=ids)  # type: ignore[assignment]
        try:
            await rp.add_remembered(it, view, AsyncMock(), 1)
        finally:
            rp.remembered_ids = original  # type: ignore[assignment]

    def view_with_one_item(self) -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Back"))
        return view

    async def test_with_the_switch_off_the_picker_is_exactly_what_it_was(self) -> None:
        here = guild(member(ALICE, "Alice"))
        view = self.view_with_one_item()
        before = list(view.children)
        await self.run_add(self.interaction(on=False, here=here, ids=[ALICE]), [ALICE], view)
        self.assertEqual(list(view.children), before)

    async def test_with_it_on_the_remembered_menu_comes_first_and_is_marked(self) -> None:
        here = guild(member(ALICE, "Alice"), member(BOB, "Bob"))
        view = self.view_with_one_item()
        await self.run_add(self.interaction(on=True, here=here, ids=[ALICE]), [ALICE, BOB], view)
        first = view.children[0]
        self.assertIsInstance(first, rp.RememberedSelect)
        assert isinstance(first, rp.RememberedSelect)
        self.assertEqual([o.label for o in first.options], ["Alice", "Bob"])
        self.assertTrue(all(o.description == "played with you before" for o in first.options))
        self.assertEqual(len(view.children), 2)  # (the picker's own items follow)

    async def test_nobody_in_the_server_adds_nothing(self) -> None:
        view = self.view_with_one_item()
        before = list(view.children)
        await self.run_add(self.interaction(on=True, here=guild(), ids=[]), [ALICE], view)
        self.assertEqual(list(view.children), before)

    async def test_a_failure_never_breaks_the_picker(self) -> None:
        view = self.view_with_one_item()
        before = list(view.children)
        it = self.interaction(on=True, here=guild(member(ALICE, "Alice")), ids=[ALICE])
        original = rp.remembered_ids
        rp.remembered_ids = AsyncMock(side_effect=RuntimeError("Alice"))  # type: ignore[assignment]
        try:
            with self.assertLogs("dmbot.ui.remembered_players", level="WARNING") as logged:
                await rp.add_remembered(it, view, AsyncMock(), 1)
        finally:
            rp.remembered_ids = original  # type: ignore[assignment]
        self.assertEqual(list(view.children), before)
        self.assertNotIn("Alice", logged.output[0])  # (the type, never a name)

    async def test_picking_one_does_what_picking_them_in_discords_picker_does(self) -> None:
        alice = member(ALICE, "Alice")
        picked = AsyncMock()
        select = rp.RememberedSelect(guild(alice), [alice], picked)
        object.__setattr__(select, "_values", [str(ALICE)])  # (what Discord filled in)
        it = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        await select.callback(it)  # type: ignore[arg-type]
        picked.assert_awaited_once_with(it, alice)

    async def test_one_who_has_left_since_it_was_listed_is_refused(self) -> None:
        alice = member(ALICE, "Alice")
        picked = AsyncMock()
        select = rp.RememberedSelect(guild(), [alice], picked)  # (the server has no Alice now)
        object.__setattr__(select, "_values", [str(ALICE)])
        send = AsyncMock()
        await select.callback(SimpleNamespace(response=SimpleNamespace(send_message=send)))  # type: ignore[arg-type]
        picked.assert_not_awaited()
        self.assertEqual(send.await_args.args[0], rp.GONE)


class TheList(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for user in (DM, OTHER_DM, ALICE, BOB, CAL):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        self.campaigns = CampaignStore(self.db)
        self.memory = MemoryStore(self.db)

    async def plays(self, dm: int, name: str, player: int) -> None:
        self.n = getattr(self, "n", 0) + 1
        campaign = await self.campaigns.create(1, f"Table {self.n} of {dm}", dm)
        await self.memory.add_entity(
            1, campaign.id, name=name, type="character", role="player_character",
            played_by=player, status="confirmed", source="dm",
        )  # fmt: skip

    async def test_a_dm_reads_only_their_own_list_the_latest_first(self) -> None:
        await self.plays(DM, "Cerric", ALICE)
        await self.plays(DM, "Belleros", BOB)
        await self.plays(OTHER_DM, "Orla", CAL)  # (another DM's player)
        mine = await rp.remembered_ids(self.db, DM)
        self.assertEqual(set(mine), {ALICE, BOB})
        self.assertNotIn(CAL, mine)
        self.assertEqual(await rp.remembered_ids(self.db, OTHER_DM), [CAL])
        self.assertEqual(await rp.remembered_ids(self.db, ALICE), [])  # (a player has no list)


if __name__ == "__main__":
    unittest.main()
