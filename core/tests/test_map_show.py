"""A DM shows a kept map to the table or hides it in Discord (#4414): the warning comes before
showing, one press changes it, Undo takes it back, only a DM may press, and a map DMbot may not
use is not listed. The real stores on a test database; Discord is a stand-in. Needs Postgres."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot.campaigns import CampaignStore
from dmbot.maps import words
from dmbot.maps.store import DM_ONLY, GONE, TABLE, MapStore, Viewer
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, PictureStore
from dmbot.ui import map_show
from tests.pg import DatabaseTest

GUILD = 1
DM, DM2, PLAYER, STRANGER = 7, 6, 8, 9


class Showing(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.bot = SimpleNamespace(
            map_store=self.maps, memory=self.memory, campaigns=self.campaigns
        )
        self.n = 0

    async def make(self, title: str, *, usable: bool = True) -> str:
        self.n += 1
        added = await self.pictures.add(
            GUILD, self.campaign.id, poster_id=DM, session_id="", line_id=None,
            message_id=self.n, description="", consent_version=7, right_granted_at=900,
            sha256=f"{self.n:064x}", object_key=f"p/{self.n}.png", preview_key="",
            size_bytes=MB, preview_bytes=0, storage_mb=5,
        )  # fmt: skip
        assert added.picture is not None
        made = await self.maps.add_map(
            GUILD, self.campaign.id, picture_id=added.picture.id,
            uploader_id=DM if usable else PLAYER,
            uploader_is_dm=usable, right_to_use=True, kind="other", came_from="discord",
            title=title, storage_mb=5,
        )  # fmt: skip
        return made.value.id

    def press(self, user: int) -> Any:
        return SimpleNamespace(
            user=SimpleNamespace(id=user),
            client=self.bot,
            type=discord.InteractionType.component,
            response=SimpleNamespace(is_done=lambda: True, defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
            data={},
        )

    async def dm_only(self, interaction: Any, campaign_id: str) -> Any:
        campaign = await self.campaigns.get(GUILD, campaign_id)
        assert campaign is not None
        return campaign if interaction.user.id in campaign.dm_user_ids else None

    async def act(self, user: int, act: str, map_id: str) -> Any:
        it = self.press(user)
        with patch("dmbot.ui.map_show._dm_campaign", new=self.dm_only):
            await map_show.ActButton(act, self.campaign.id, map_id).callback(it)
        return it

    @staticmethod
    def said(it: Any) -> str:
        return str(it.edit_original_response.await_args.kwargs["content"])

    @staticmethod
    def labels(it: Any) -> list[str]:
        view = it.edit_original_response.await_args.kwargs["view"]
        return [str(getattr(c, "item", c).label) for c in view.children]

    async def audience(self, map_id: str) -> str:
        found = await self.maps.get(GUILD, self.campaign.id, map_id, Viewer(dm=True))
        assert found is not None
        return found.audience

    async def test_showing_asks_first_and_changes_nothing_until_yes(self) -> None:
        map_id = await self.make("The Keep")
        asked = await self.act(DM, "s", map_id)
        self.assertEqual(self.said(asked), words.SHOW_SURE)
        self.assertEqual(self.labels(asked), [words.SHOW_YES_LABEL, words.BACK_LABEL])
        self.assertEqual(await self.audience(map_id), DM_ONLY)

    async def test_yes_shows_it_with_an_undo_and_the_card_then_offers_hide(self) -> None:
        map_id = await self.make("The Keep")
        done = await self.act(DM, "y", map_id)
        self.assertEqual(await self.audience(map_id), TABLE)
        self.assertIn(words.NOW_SHOWN, self.said(done))
        self.assertIn(words.SEEN_NOW_TABLE, self.said(done))
        self.assertEqual(self.labels(done), [words.HIDE_LABEL, words.BACK_LABEL, words.UNDO_LABEL])

    async def test_hide_hides_it_and_undo_shows_it_again(self) -> None:
        map_id = await self.make("The Keep")
        await self.act(DM, "y", map_id)
        hidden = await self.act(DM, "h", map_id)
        self.assertEqual(await self.audience(map_id), DM_ONLY)
        self.assertIn(words.NOW_HIDDEN, self.said(hidden))
        # the Undo button on that card takes the hide back (one change in the log)
        view = hidden.edit_original_response.await_args.kwargs["view"]
        undo = next(c for c in view.children if getattr(c, "batch", None))
        again = self.press(DM)
        with patch("dmbot.ui.map_show._dm_campaign", new=self.dm_only):
            await undo.callback(again)
        self.assertEqual(await self.audience(map_id), TABLE)
        self.assertIn(words.UNDONE, self.said(again))

    async def test_only_a_dm_may_press_and_nothing_changes_for_anyone_else(self) -> None:
        map_id = await self.make("The Keep")
        for who in (PLAYER, STRANGER):
            for act in ("s", "y", "h"):
                await self.act(who, act, map_id)
        self.assertEqual(await self.audience(map_id), DM_ONLY)

    async def test_a_second_dm_may_show_it_too(self) -> None:
        await self.campaigns.add_dm(GUILD, self.campaign.id, DM2)
        map_id = await self.make("The Keep")
        await self.act(DM2, "y", map_id)
        self.assertEqual(await self.audience(map_id), TABLE)

    async def test_a_map_that_is_gone_says_so(self) -> None:
        map_id = await self.make("The Keep")
        await self.maps.remove_map(GUILD, self.campaign.id, map_id)
        gone = await self.act(DM, "y", map_id)
        self.assertEqual(self.said(gone), GONE)

    async def test_the_list_holds_only_maps_dmbot_may_use_and_marks_the_shown_ones(self) -> None:
        mine = await self.make("The Keep")
        await self.make("Waiting for a yes", usable=False)
        await self.maps.set_audience(GUILD, self.campaign.id, mine, TABLE)
        it = self.press(DM)
        with patch("dmbot.ui.map_show._dm_campaign", new=self.dm_only):
            await map_show._list(it, self.campaign.id)
        view = it.edit_original_response.await_args.kwargs["view"]
        (menu,) = [cast(Any, c).item for c in view.children]
        self.assertEqual([o.label for o in menu.options], ["The Keep (shown)"])

    async def test_no_maps_says_so_and_a_title_with_markdown_and_pings_is_made_harmless(
        self,
    ) -> None:
        it = self.press(DM)
        with patch("dmbot.ui.map_show._dm_campaign", new=self.dm_only):
            await map_show._list(it, self.campaign.id)
        self.assertIn(words.NO_MAPS, self.said(it))
        self.assertNotIn("@everyone", map_show._title("**hi** @everyone"))
        self.assertEqual(map_show._title("   "), words.UNTITLED)
