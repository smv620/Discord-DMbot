"""A DM's remembered players in Discord's player pickers (#4724, the Discord half of #4226): the
DM's own list first, marked, only people in the server now, never another DM's, nothing at all with
the switch off. Stand-ins for Discord; the reader of the list runs against a real database."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot.campaigns import CampaignStore
from dmbot.memory.store import MemoryStore
from dmbot.ui import remembered_players as rp
from tests.pg import DatabaseTest

DM, OTHER_DM, ALICE, BOB, CAL, BOT = 7001, 7002, 8001, 8002, 8003, 8099


def member(user_id: int, name: str, *, bot: bool = False) -> Any:
    return SimpleNamespace(id=user_id, display_name=name, bot=bot)


def guild(*members: Any, cached: tuple[int, ...] = (), away: tuple[int, ...] = ()) -> Any:
    """A server stand-in. `cached`: the ids its cache holds (all of them when empty and `away` is
    empty too). The others are found only by asking Discord (`fetch_member`), as a quiet member is;
    `away` ids aren't in the server at all."""
    by_id = {m.id: m for m in members if m.id not in away}
    holds = set(cached) if cached else set(by_id)
    asked: list[int] = []

    async def fetch_member(user_id: int) -> Any:
        asked.append(user_id)
        if user_id in by_id:
            return by_id[user_id]
        gone = SimpleNamespace(status=404, reason="gone")
        raise discord.NotFound(cast(Any, gone), "unknown member")

    return SimpleNamespace(
        get_member=lambda user_id: by_id.get(user_id) if user_id in holds else None,
        fetch_member=fetch_member,
        asked=asked,
    )


class TheMembers(unittest.IsolatedAsyncioTestCase):
    async def test_only_people_in_the_server_now_in_the_lists_order_never_a_bot_or_the_dm(
        self,
    ) -> None:
        here = guild(
            member(BOB, "Bob"), member(ALICE, "Alice"), member(BOT, "Bot", bot=True),
            member(DM, "The DM"),
        )  # fmt: skip
        got = await rp.members_of(here, [ALICE, 1234, BOT, DM, BOB], DM)
        self.assertEqual([m.id for m in got], [ALICE, BOB])  # (1234 has left; no bot, no DM)

    async def test_a_quiet_member_the_cache_doesnt_hold_is_asked_of_discord(self) -> None:
        # (DMbot has no members intent: the cache holds only people it has seen lately)
        here = guild(member(ALICE, "Alice"), member(BOB, "Bob"), cached=(ALICE,))
        got = await rp.members_of(here, [BOB, ALICE], DM)
        self.assertEqual([m.id for m in got], [BOB, ALICE])
        self.assertEqual(here.asked, [BOB])  # (the cached one wasn't asked)

    async def test_someone_who_left_is_skipped_not_an_error(self) -> None:
        here = guild(member(ALICE, "Alice"), member(BOB, "Bob"), away=(BOB,))
        got = await rp.members_of(here, [BOB, ALICE], DM)
        self.assertEqual([m.id for m in got], [ALICE])

    async def test_only_the_people_in_only_are_listed(self) -> None:
        here = guild(member(ALICE, "Alice"), member(BOB, "Bob"))
        got = await rp.members_of(here, [ALICE, BOB], DM, only={BOB})
        self.assertEqual([m.id for m in got], [BOB])

    async def test_at_most_one_menus_worth(self) -> None:
        many = [member(9000 + i, f"P{i}") for i in range(40)]
        got = await rp.members_of(guild(*many), [m.id for m in many], DM)
        self.assertEqual(len(got), rp.MAX_OPTIONS)

    async def test_too_slow_a_lookup_lists_nobody(self) -> None:
        async def slow(user_id: int) -> Any:
            await asyncio.sleep(5)

        here = SimpleNamespace(get_member=lambda user_id: None, fetch_member=slow)
        with patch.object(rp, "LOOK_S", 0.05):
            got = await rp.members_of(here, [ALICE], DM)  # type: ignore[arg-type]
        self.assertEqual(got, [])


class TheMenu(unittest.IsolatedAsyncioTestCase):
    def interaction(self, *, on: bool, here: Any, ids: list[int]) -> Any:
        client = SimpleNamespace(
            settings=SimpleNamespace(remembered_players_on=on),
            get_guild=lambda guild_id: here,
            campaigns=SimpleNamespace(db=object()),
        )
        return SimpleNamespace(client=client, user=SimpleNamespace(id=DM))

    async def run_add(self, it: Any, ids: list[int], view: discord.ui.View) -> None:
        with patch.object(rp, "remembered_ids", AsyncMock(return_value=ids)):
            await rp.add_remembered(it, view, AsyncMock(), 1)

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
        with (
            patch.object(rp, "remembered_ids", AsyncMock(side_effect=RuntimeError("Alice"))),
            self.assertLogs("dmbot.ui.remembered_players", level="WARNING") as logged,
        ):
            await rp.add_remembered(it, view, AsyncMock(), 1)
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
        self.assertEqual(send.await_args_list[-1].args[0], rp.GONE)


class TheCallSites(unittest.IsolatedAsyncioTestCase):
    """The real picker with the menu put above it: the order, and a pick reaching the picker."""

    def interaction(self, here: Any) -> Any:
        client = SimpleNamespace(
            settings=SimpleNamespace(remembered_players_on=True),
            get_guild=lambda guild_id: here,
            campaigns=SimpleNamespace(db=object()),
        )
        response = SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock())
        return SimpleNamespace(client=client, user=SimpleNamespace(id=DM), response=response)

    async def test_the_real_player_picker_gets_the_menu_first_and_a_pick_opens_its_form(
        self,
    ) -> None:
        from dmbot.ui import names

        alice = member(ALICE, "Alice")
        it = self.interaction(guild(alice))
        view = names.PlayerPicker("c" * 32, 1)
        before = len(view.children)
        with patch.object(rp, "remembered_ids", AsyncMock(return_value=[ALICE])):
            await rp.add_remembered(it, view, view.picked, 1)
        self.assertIsInstance(view.children[0], rp.RememberedSelect)
        self.assertEqual(len(view.children), before + 1)  # (the picker's own items all follow)
        select = view.children[0]
        assert isinstance(select, rp.RememberedSelect)
        object.__setattr__(select, "_values", [str(ALICE)])
        with patch.object(names, "_campaign_for", AsyncMock(return_value=object())):
            await select.callback(it)
        form = it.response.send_modal.await_args.args[0]
        self.assertIsInstance(form, names.CharacterForm)

    async def test_a_menu_with_no_room_leaves_the_picker_as_it_was(self) -> None:
        view = discord.ui.View()
        for _ in range(5):  # five full rows: no room for a sixth
            view.add_item(discord.ui.Select(options=[discord.SelectOption(label="x")]))
        before = list(view.children)
        it = self.interaction(guild(member(ALICE, "Alice")))
        with patch.object(rp, "remembered_ids", AsyncMock(return_value=[ALICE])):
            await rp.add_remembered(it, view, AsyncMock(), 1)
        self.assertEqual(list(view.children), before)

    async def test_no_server_or_a_bot_picked_lists_and_picks_nothing(self) -> None:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Back"))
        it = self.interaction(None)  # (the bot isn't in that server)
        with patch.object(rp, "remembered_ids", AsyncMock(return_value=[ALICE])):
            await rp.add_remembered(it, view, AsyncMock(), 1)
        self.assertEqual(len(view.children), 1)
        bot = member(BOT, "Bot", bot=True)
        picked = AsyncMock()
        select = rp.RememberedSelect(guild(bot), [member(ALICE, "Alice")], picked)
        object.__setattr__(select, "_values", [str(BOT)])
        send = AsyncMock()
        await select.callback(SimpleNamespace(response=SimpleNamespace(send_message=send)))  # type: ignore[arg-type]
        picked.assert_not_awaited()
        self.assertEqual(send.await_args_list[-1].args[0], rp.GONE)


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

    async def test_a_dm_reads_only_their_own_list(self) -> None:
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
