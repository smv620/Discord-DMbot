"""The DM's side of the map question (#4344, slice 3b-2a): a map a player kept is counted for the
Questions message; a DM answers it (Yes lets DMbot use it, No keeps the picture unused, Remove
deletes it); only a DM may press; the first answer stands and a second DM is told who answered; the
card moves on to the next map. The real stores on a test database; Discord is a stand-in. Needs
Postgres."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import discord

from dmbot.campaigns import CampaignStore
from dmbot.maps import words
from dmbot.maps.store import GONE, AlreadyAnswered, MapStore, Viewer, may_use_map
from dmbot.memory.store import MemoryStore
from dmbot.pictures.keeper import PictureKeeper
from dmbot.pictures.store import MB, PictureStore
from dmbot.questions import board, kinds
from dmbot.questions.counts import Sources, count_kinds
from dmbot.ui import map_answer
from tests.pg import DatabaseTest

GUILD = 1
DM, DM2, PLAYER, STRANGER = 7, 6, 8, 9


class Bucket:
    async def put(self, key: str, data: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...


class Answering(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.members = {PLAYER: "Mia", DM: "Ada", DM2: "Bo"}
        guild = SimpleNamespace(
            get_member=lambda uid: SimpleNamespace(display_name=self.members.get(uid))
        )
        self.bot = SimpleNamespace(
            picture_store=self.pictures,
            picture_keeper=PictureKeeper(self.pictures, Bucket(), 5),
            map_store=self.maps,
            memory=self.memory,
            campaigns=self.campaigns,
            settings=SimpleNamespace(site_url="https://example.org", picture_storage_mb=5),
            get_guild=lambda gid: guild,
        )
        self.n = 0

    async def waiting_map(self) -> str:
        self.n += 1
        added = await self.pictures.add(
            GUILD, self.campaign.id, poster_id=PLAYER, session_id="", line_id=None,
            message_id=self.n, description="", consent_version=7, right_granted_at=900,
            sha256=f"{self.n:064x}", object_key=f"p/{self.n}.png", preview_key="",
            size_bytes=MB, preview_bytes=0, storage_mb=5,
        )  # fmt: skip
        assert added.picture is not None
        made = await self.maps.add_map(
            GUILD, self.campaign.id, picture_id=added.picture.id, uploader_id=PLAYER,
            uploader_is_dm=False, right_to_use=True, kind="other", came_from="discord",
            audience="table", storage_mb=5,
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
        )

    async def act(self, user: int, act: str, map_id: str) -> Any:
        it = self.press(user)
        with patch("dmbot.ui.map_answer._dm_campaign", new=self.dm_only):
            await map_answer.ActButton(act, self.campaign.id, map_id).callback(it)
        return it

    async def dm_only(self, interaction: Any, campaign_id: str) -> Any:
        campaign = await self.campaigns.get(GUILD, campaign_id)
        assert campaign is not None
        return campaign if interaction.user.id in campaign.dm_user_ids else None

    @staticmethod
    def shown(it: Any) -> str:
        return str(it.edit_original_response.await_args.kwargs["content"])

    async def test_a_waiting_map_is_counted_for_the_questions_message(self) -> None:
        await self.waiting_map()
        await self.waiting_map()
        src = Sources(db=self.db, maps=self.maps)
        found = await count_kinds(src, GUILD, self.campaign.id, only={kinds.MAPS})
        self.assertEqual(found[kinds.MAPS].n, 2)
        self.assertEqual(
            [i.item.custom_id for i in board.view(GUILD, self.campaign.id, found).children],
            [f"dmbot:mapsq:open:{self.campaign.id}"],
        )

    async def test_a_yes_lets_dmbot_use_it_and_the_card_moves_to_the_next_map(self) -> None:
        first, second = await self.waiting_map(), await self.waiting_map()
        it = await self.act(DM, "y", first)
        mine = await self.maps.get(GUILD, self.campaign.id, first, Viewer(dm=True))
        assert mine is not None
        self.assertTrue(may_use_map(mine))
        self.assertIn(words.dm_title("Mia"), self.shown(it))  # (the next card: the second map)
        self.assertEqual(
            it.edit_original_response.await_args.kwargs["view"]
            .children[0]
            .item.custom_id.split(":")[-1],
            second,
        )

    async def test_a_no_keeps_the_picture_unused_and_the_last_answer_says_nothing_waits(
        self,
    ) -> None:
        only = await self.waiting_map()
        it = await self.act(DM, "n", only)
        kept = await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True))
        assert kept is not None
        self.assertFalse(may_use_map(kept))
        self.assertTrue(self.shown(it).endswith(words.NOTHING_WAITING))
        self.assertTrue(self.shown(it).startswith(words.done_no()))

    async def test_remove_takes_only_the_map_and_the_picture_stays(self) -> None:
        only = await self.waiting_map()
        removed = await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True))
        assert removed is not None
        it = await self.act(DM, "r", only)
        self.assertIsNone(await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True)))
        kept = await self.pictures.by_id(GUILD, removed.picture_id)
        assert kept is not None
        self.assertFalse(kept.gone)  # (no confirmation needed: it is one undoable change)
        self.assertTrue(self.shown(it).startswith(words.done_removed()))

    async def undo(self, user: int, batch: int) -> Any:
        it = self.press(user)
        with patch("dmbot.ui.map_answer._dm_campaign", new=self.dm_only):
            await map_answer.UndoButton(self.campaign.id, batch).callback(it)
        return it

    @staticmethod
    def batch_of(it: Any) -> int:
        view = it.edit_original_response.await_args.kwargs["view"]
        undo = [c.item for c in view.children if c.item.custom_id.startswith("dmbot:mapsq:u:")]
        return int(undo[0].custom_id.rsplit(":", 1)[1])

    async def test_a_yes_a_no_and_a_remove_can_each_be_undone(self) -> None:
        a, b, c = await self.waiting_map(), await self.waiting_map(), await self.waiting_map()
        yes = await self.act(DM, "y", a)
        await self.undo(DM, self.batch_of(yes))
        first = await self.maps.get(GUILD, self.campaign.id, a, Viewer(dm=True))
        assert first is not None
        self.assertTrue(first.waiting)  # (back to waiting for a yes)
        no = await self.act(DM, "n", b)
        back = await self.undo(DM, self.batch_of(no))
        self.assertTrue(self.shown(back).startswith(words.UNDONE))
        second = await self.maps.get(GUILD, self.campaign.id, b, Viewer(dm=True))
        assert second is not None
        self.assertTrue(second.waiting)
        gone = await self.act(DM, "r", c)
        await self.undo(DM, self.batch_of(gone))
        self.assertIsNotNone(await self.maps.get(GUILD, self.campaign.id, c, Viewer(dm=True)))

    async def test_only_a_dm_may_undo_and_a_second_undo_says_so(self) -> None:
        only = await self.waiting_map()
        yes = await self.act(DM, "y", only)
        batch = self.batch_of(yes)
        await self.undo(STRANGER, batch)
        still = await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True))
        assert still is not None
        self.assertTrue(may_use_map(still))  # (a stranger's press did nothing)
        await self.undo(DM, batch)
        again = await self.undo(DM, batch)
        self.assertEqual(self.shown(again), words.UNDO_FAILED)

    async def test_only_a_dm_may_press(self) -> None:
        only = await self.waiting_map()
        await self.act(STRANGER, "y", only)
        again = await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True))
        assert again is not None
        self.assertTrue(again.waiting)

    async def test_the_first_answer_stands_and_a_second_dm_is_told(self) -> None:
        only = await self.waiting_map()
        await self.act(DM, "n", only)
        late = await self.act(DM, "y", only)
        self.assertEqual(self.shown(late), words.answered_by("Ada", False))
        kept = await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True))
        assert kept is not None
        self.assertFalse(may_use_map(kept))

    async def test_a_gone_map_says_so(self) -> None:
        it = await self.act(DM, "y", "0" * 32)
        self.assertEqual(self.shown(it), GONE)

    async def test_the_store_leaves_the_first_answer_when_two_dms_press_at_once(self) -> None:
        only = await self.waiting_map()
        first = await self.maps.answer_use(
            GUILD, self.campaign.id, only, dm_id=DM, yes=True, first_only=True
        )
        self.assertTrue(may_use_map(first.value))
        with self.assertRaises(AlreadyAnswered) as late:
            await self.maps.answer_use(
                GUILD, self.campaign.id, only, dm_id=DM2, yes=False, first_only=True
            )
        self.assertEqual((late.exception.map.use_by, late.exception.map.use_ok), (DM, True))
        kept = await self.maps.get(GUILD, self.campaign.id, only, Viewer(dm=True))
        assert kept is not None
        self.assertTrue(may_use_map(kept))  # (the late no did not turn the yes round)

    async def test_the_dm_sees_what_their_answer_did(self) -> None:
        a, b = await self.waiting_map(), await self.waiting_map()
        yes = await self.act(DM, "y", a)
        self.assertTrue(self.shown(yes).startswith(words.done_yes()))
        removed = await self.act(DM, "r", b)
        self.assertTrue(self.shown(removed).startswith(words.done_removed()))
