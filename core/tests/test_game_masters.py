"""Adding and removing a campaign's game masters from ⚙️ Settings (#1600, item 2 of #618): DMs only
on every press, a bot / a duplicate / the cap refused, the plan holder and the last game master
kept, self-removal allowed, a private message to a new game master, no change to anyone's consent,
the DM screen's access re-applied, and a running table's DM set following at once."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import LAST_DM, MAX_DMS, OWNER_STAYS, TOO_MANY_DMS
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.dm_screen import game_masters as gm
from dmbot.dm_screen.channel import DMScreenError
from dmbot.dm_screen.settings import management_view
from dmbot.sessions import SessionStore
from tests.pg import DatabaseTest

GUILD, OWNER, CO, NEWBIE, MANAGER, PLAYER = 1, 7, 8, 9, 10, 11
NAMES = {OWNER: "Sam", CO: "Kit", NEWBIE: "Nia", MANAGER: "Max", PLAYER: "Pip"}


class Response:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.edited: list[tuple[str, Any]] = []

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, text: str = "", **kw: Any) -> None:
        self.done = True
        self.sent.append((text, kw))

    async def defer(self, **_: Any) -> None:
        self.done = True

    async def edit_message(self, *, content: str = "", view: Any = None, **_: Any) -> None:
        self.done = True
        self.edited.append((content, view))


class GameMasters(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            SessionStore(self.db),
        )
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", OWNER)
        await self.campaigns.add_dm(GUILD, self.campaign.id, CO)
        async with self.db.guild(GUILD) as conn:  # whose plan it uses
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE guild_id = %s AND id = %s",
                (OWNER, GUILD, self.campaign.id),
            )
        self.sent_to: dict[int, list[str]] = {}
        self.unreachable: set[int] = set()
        self.members: dict[int, Any] = {uid: self.member(uid) for uid in NAMES}
        self.guild = SimpleNamespace(
            id=GUILD,
            name="The Table",
            get_member=self.members.get,
            fetch_member=AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone")),
        )
        self.screen = SimpleNamespace(id=500)

        async def applied(guild: Any, cid: str, store: Any, **_: Any) -> Any:
            """Like setup_dm_screen: the campaign as stored when the screen was set up."""
            return SimpleNamespace(channel=self.screen, campaign=await store.get(GUILD, cid))

        self.access = AsyncMock(side_effect=applied)
        self.hook = AsyncMock()
        self.bot.after_screen_change = self.hook  # type: ignore[method-assign]
        patcher = patch.object(gm, "setup_dm_screen", self.access)
        patcher.start()
        self.addCleanup(patcher.stop)

    def member(self, uid: int) -> Any:
        async def send(text: str, **_: Any) -> None:
            if uid in self.unreachable:
                raise discord.Forbidden(MagicMock(status=403), "closed")
            self.sent_to.setdefault(uid, []).append(text)

        return SimpleNamespace(
            id=uid, display_name=NAMES[uid], name=NAMES[uid], bot=False, send=send
        )

    def it(self, user_id: int = OWNER) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            user=SimpleNamespace(id=user_id, display_name=NAMES[user_id], name=NAMES[user_id]),
            response=Response(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def hook_campaign(self) -> Any:
        """The campaign the running session was told about (the last `after_screen_change`)."""
        call = self.hook.await_args
        assert call is not None
        return call.args[0]

    async def dms(self) -> frozenset[int]:
        got = await self.campaigns.get(GUILD, self.campaign.id)
        assert got is not None
        return got.dm_user_ids

    def said(self, it: Any) -> str:
        if it.followup.send.await_args_list:
            return str(it.followup.send.await_args_list[-1].args[0])
        return str(it.response.sent[-1][0])

    async def add(self, who: int = NEWBIE, *, by: int = OWNER, bot: bool = False) -> Any:
        await self.dms()
        picker = gm.AddPicker(self.campaign.id)
        person = self.members.get(who) or SimpleNamespace(id=who, display_name="x", name="x")
        person.bot = bot
        picker.pick = SimpleNamespace(values=[person])  # type: ignore[assignment]
        it = self.it(by)
        await picker._picked(it)
        return it

    # ---- who sees it ----

    async def test_the_button_is_only_for_the_campaigns_dms(self) -> None:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        for viewer, expected in ((OWNER, True), (CO, True), (MANAGER, False), (PLAYER, False)):
            view = management_view(campaign, None, viewer)
            has = any(isinstance(c, gm.GameMastersButton) for c in view.children)
            self.assertEqual(has, expected, viewer)

    async def test_a_press_by_someone_who_is_not_a_dm_is_refused_even_with_the_button(
        self,
    ) -> None:
        for viewer in (MANAGER, PLAYER):
            it = self.it(viewer)
            await gm.open_list(it, self.campaign.id)
            self.assertEqual(it.response.sent[0][0], gm.ONLY_DMS)

    async def test_the_list_names_the_game_masters_and_marks_the_plan(self) -> None:
        it = self.it(CO)
        await gm.open_list(it, self.campaign.id)
        text = it.response.sent[0][0]
        self.assertIn("**Sam** (plan)", text)
        self.assertIn("**Kit**", text)
        self.assertNotIn("**Kit** (plan)", text)

    # ---- add ----

    async def test_adding_a_game_master_tells_them_and_follows_access_at_once(self) -> None:
        it = await self.add()
        self.assertEqual(await self.dms(), frozenset({OWNER, CO, NEWBIE}))
        (message,) = self.sent_to[NEWBIE]
        self.assertIn("**Sam** made you a game master of **Frostmaiden** in **The Table**", message)
        self.assertIn("You can now see its DM screen and change its settings.", message)
        self.access.assert_awaited_once()
        self.hook.assert_awaited_once()
        self.assertIn(NEWBIE, self.hook_campaign().dm_user_ids)
        done = it.edit_original_response.await_args.kwargs["content"]
        self.assertIn("**Nia** is now a game master of **Frostmaiden**", done)

    async def test_adding_never_touches_their_consent(self) -> None:
        await self.consent.grant(GUILD, CO)
        before = await self.consent.stored(GUILD)
        await self.add()
        self.assertEqual(await self.consent.stored(GUILD), before)
        self.assertNotIn(NEWBIE, before)  # not made to agree by being added
        self.assertFalse(self.consent.has_consent(GUILD, NEWBIE))

    async def test_a_bot_and_someone_who_is_already_a_dm_are_refused_with_nothing_written(
        self,
    ) -> None:
        it = await self.add(NEWBIE, bot=True)
        self.assertEqual(self.said(it), gm.NOT_A_PERSON)
        it = await self.add(CO)
        self.assertIn("**Kit** is already a game master", self.said(it))
        self.assertEqual(await self.dms(), frozenset({OWNER, CO}))
        self.access.assert_not_awaited()

    async def test_the_cap_is_five_in_the_menu_and_in_the_store(self) -> None:
        for extra in (100, 101, 102):
            await self.campaigns.add_dm(GUILD, self.campaign.id, extra)
        self.assertEqual(len(await self.dms()), MAX_DMS)
        it = await self.add()
        self.assertEqual(self.said(it), TOO_MANY_DMS)
        with self.assertRaises(Exception) as caught:
            await self.campaigns.add_dm(GUILD, self.campaign.id, NEWBIE)
        self.assertEqual(str(caught.exception), TOO_MANY_DMS)
        await self.campaigns.add_dm(GUILD, self.campaign.id, CO)  # already one: no change, no error

    async def test_only_a_dm_can_add_even_from_an_old_menu(self) -> None:
        it = await self.add(NEWBIE, by=MANAGER)
        self.assertEqual(self.said(it), gm.ONLY_DMS)
        self.assertNotIn(NEWBIE, await self.dms())

    async def test_a_new_dm_who_cannot_be_messaged_is_still_added_and_the_adder_is_told(
        self,
    ) -> None:
        self.unreachable.add(NEWBIE)
        it = await self.add()
        self.assertIn(NEWBIE, await self.dms())
        self.assertIn(gm.NOT_TOLD.strip(), it.edit_original_response.await_args.kwargs["content"])

    async def test_if_the_screen_cannot_be_updated_the_change_stays_and_the_dm_is_told(
        self,
    ) -> None:
        self.access.side_effect = DMScreenError("no permission")
        it = await self.add()
        self.assertIn(NEWBIE, await self.dms())
        self.assertIn(
            "couldn't update who can see the DM screen yet",
            (it.edit_original_response.await_args.kwargs["content"]),
        )

    # ---- remove ----

    async def pick_remove(self, who: int, *, by: int = OWNER) -> Any:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        picker = gm.RemovePicker(
            campaign, await gm.names_of(cast(Any, self.guild), campaign.dm_user_ids)
        )
        picker.pick = SimpleNamespace(values=[str(who)])  # type: ignore[assignment]
        it = self.it(by)
        await picker._picked(it)
        return it

    async def confirm(
        self,
        who: int,
        *,
        by: int = OWNER,
    ) -> Any:
        confirm = gm.ConfirmRemove(self.campaign.id, who, NAMES[who])
        it = self.it(by)
        await confirm._yes(it)
        return it

    async def test_removing_asks_once_then_takes_the_screen_away_at_once(self) -> None:
        it = await self.pick_remove(CO)
        text, view = it.response.edited[0]
        self.assertEqual(
            text, "**Kit** will no longer see the DM screen or change this campaign. Remove?"
        )
        self.assertEqual([str(c.label) for c in view.children], ["Cancel", "Remove"])
        self.assertEqual(await self.dms(), frozenset({OWNER, CO}))  # nothing yet
        done = await self.confirm(CO)
        self.assertEqual(await self.dms(), frozenset({OWNER}))
        self.assertIn(
            "**Kit** is no longer a game master of **Frostmaiden**",
            (done.edit_original_response.await_args.kwargs["content"]),
        )
        self.access.assert_awaited_once()
        self.assertNotIn(CO, self.hook_campaign().dm_user_ids)

    async def test_cancel_changes_nothing(self) -> None:
        confirm = gm.ConfirmRemove(self.campaign.id, CO, "Kit")
        it = self.it()
        await confirm._no(it)
        self.assertEqual(await self.dms(), frozenset({OWNER, CO}))
        self.access.assert_not_awaited()

    async def test_the_plan_holder_cannot_be_removed_and_the_words_fit_who_pressed(self) -> None:
        other = await self.pick_remove(
            OWNER, by=CO
        )  # a co-DM: told who holds it and what they can do
        self.assertIn(
            "**Sam** holds this campaign's plan, so they can't be removed.", self.said(other)
        )
        self.assertIn("🤝 Hand over", self.said(other))
        mine = await self.pick_remove(OWNER, by=OWNER)  # the plan holder: they have the button
        self.assertEqual(self.said(mine), gm.PLAN_HOLDER_SELF)
        # and the store still refuses it if a stale menu gets that far
        done = await self.confirm(OWNER, by=CO)
        self.assertIn("holds this campaign's plan", self.said(done))
        self.assertNotEqual(OWNER_STAYS, self.said(done))  # DMbot's own words, not the store's
        self.assertIn(OWNER, await self.dms())

    async def test_the_last_game_master_cannot_be_removed(self) -> None:
        await self.campaigns.remove_dm(GUILD, self.campaign.id, CO)
        async with self.db.guild(GUILD) as conn:  # no plan holder: only the last-one rule is left
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.campaign.id,)
            )
        it = await self.pick_remove(OWNER)
        self.assertEqual(self.said(it), LAST_DM)
        done = await self.confirm(OWNER)
        self.assertEqual(self.said(done), LAST_DM)
        self.assertEqual(await self.dms(), frozenset({OWNER}))

    async def test_a_game_master_may_remove_themselves(self) -> None:
        it = await self.pick_remove(CO, by=CO)
        self.assertEqual(it.response.edited[0][0], gm.CONFIRM_SELF)
        done = await self.confirm(CO, by=CO)
        self.assertEqual(await self.dms(), frozenset({OWNER}))
        self.assertIn(
            "You are no longer a game master",
            (done.edit_original_response.await_args.kwargs["content"]),
        )

    async def test_only_a_dm_can_remove_even_from_an_old_menu(self) -> None:
        done = await self.confirm(CO, by=MANAGER)
        self.assertEqual(self.said(done), gm.ONLY_DMS)
        self.assertEqual(await self.dms(), frozenset({OWNER, CO}))

    # ---- a running session ----

    async def test_a_running_table_takes_the_new_set_at_once(self) -> None:
        del self.bot.after_screen_change  # the real hook
        table = Table(
            guild_id=GUILD,
            voice_channel_id=2,
            screen_channel_id=3,
            dm_user_id=OWNER,
            segmenter=Segmenter(GUILD),
            campaign_id=self.campaign.id,
            campaign_name="Frostmaiden",
            started_at=1,
            dm_user_ids=frozenset({OWNER, CO}),
        )
        self.bot.tables[GUILD] = table
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await self.add()
        self.assertEqual(table.dm_user_ids, frozenset({OWNER, CO, NEWBIE}))
        await self.confirm(CO)
        self.assertEqual(table.dm_user_ids, frozenset({OWNER, NEWBIE}))
        self.assertFalse(table.is_dm(CO))

    async def test_a_running_table_takes_the_set_even_if_the_screen_cannot_be_updated(self) -> None:
        self.access.side_effect = DMScreenError("no permission")
        table = Table(
            guild_id=GUILD,
            voice_channel_id=2,
            screen_channel_id=3,
            dm_user_id=OWNER,
            segmenter=Segmenter(GUILD),
            campaign_id=self.campaign.id,
            campaign_name="Frostmaiden",
            started_at=1,
            dm_user_ids=frozenset({OWNER, CO}),
        )
        self.bot.tables[GUILD] = table
        await self.add()
        self.assertEqual(table.dm_user_ids, frozenset({OWNER, CO, NEWBIE}))  # authority is not late
        await self.confirm(CO)
        self.assertEqual(table.dm_user_ids, frozenset({OWNER, NEWBIE}))
        self.assertFalse(table.is_dm(CO))

    async def test_two_adds_at_once_cannot_pass_the_cap(self) -> None:
        for extra in (100, 101):
            await self.campaigns.add_dm(GUILD, self.campaign.id, extra)  # four now
        results = await asyncio.gather(
            self.campaigns.add_dm(GUILD, self.campaign.id, 200),
            self.campaigns.add_dm(GUILD, self.campaign.id, 201),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, Exception) for r in results), 1)
        self.assertEqual(len(await self.dms()), MAX_DMS)

    async def test_an_existing_game_time_channel_is_updated_too(self) -> None:
        await self.campaigns.set_gametime_channel(GUILD, self.campaign.id, 600)
        with patch.object(gm, "setup_gametime_channel", AsyncMock()) as gametime:
            await self.add()
        gametime.assert_awaited_once()


class Names(unittest.IsolatedAsyncioTestCase):
    """A game master is "someone who left the server" only when Discord says so (#2556)."""

    def guild(self, *, cached: dict[int, Any], fetch: Any) -> Any:
        return SimpleNamespace(get_member=cached.get, fetch_member=fetch)

    async def test_a_member_missing_from_the_cache_is_asked_for(self) -> None:
        mira = SimpleNamespace(display_name="Mira")
        fetch = AsyncMock(return_value=mira)
        names = await gm.names_of(self.guild(cached={}, fetch=fetch), [5])
        self.assertEqual(names, {5: "Mira"})
        fetch.assert_awaited_once_with(5)

    async def test_the_cache_is_used_first(self) -> None:
        fetch = AsyncMock()
        guild = self.guild(cached={5: SimpleNamespace(display_name="Mira")}, fetch=fetch)
        self.assertEqual(await gm.names_of(guild, [5]), {5: "Mira"})
        fetch.assert_not_awaited()

    async def test_not_found_means_they_left(self) -> None:
        fetch = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        names = await gm.names_of(self.guild(cached={}, fetch=fetch), [5])
        self.assertEqual(names, {5: "someone who left the server"})

    async def test_discord_not_answering_makes_no_claim_either_way(self) -> None:
        fetch = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "down"))
        names = await gm.names_of(self.guild(cached={}, fetch=fetch), [5])
        self.assertEqual(names, {5: "a game master"})

    async def test_no_server_at_all_is_a_game_master_too(self) -> None:
        self.assertEqual(await gm.names_of(None, [5]), {5: "a game master"})

    async def test_everyone_is_asked_at_once_and_each_gets_their_own_answer(self) -> None:
        async def fetch(uid: int) -> Any:
            if uid == 6:
                raise discord.NotFound(MagicMock(status=404), "gone")
            return SimpleNamespace(display_name=f"DM{uid}")

        names = await gm.names_of(self.guild(cached={}, fetch=fetch), [7, 5, 6])
        self.assertEqual(names, {5: "DM5", 6: "someone who left the server", 7: "DM7"})
