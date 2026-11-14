"""🔒 Make private and 🗑️ Delete under a kept picture's link (#3274): who may press, the one
confirmation, that a stranger or another server finds nothing, and that the buttons survive a
restart. The real store and actions on a test database; Discord is a stand-in. Invented ids."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.pictures.actions import GONE, PictureActions
from dmbot.pictures.store import MB, PictureStore
from dmbot.ui import kept_pictures
from tests.pg import DatabaseTest

GUILD, OTHER = 1, 2
DM, POSTER, STRANGER = 7, 8, 9


class Bucket:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes) -> None: ...

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


class Pressing(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.bucket = Bucket()
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        added = await self.store.add(
            GUILD,
            self.campaign.id,
            poster_id=POSTER,
            session_id="",
            line_id=None,
            message_id=1,
            description="a red door",
            consent_version=7,
            right_granted_at=900,
            sha256="a" * 64,
            object_key="p/one.png",
            preview_key="p/one-preview.webp",
            size_bytes=MB,
            preview_bytes=0,
            storage_mb=5,
        )
        assert added.picture is not None
        self.picture = added.picture
        self.bot = SimpleNamespace(
            picture_store=self.store,
            picture_actions=PictureActions(self.db, self.store, self.bucket, 5),
            campaigns=self.campaigns,
        )

    def press(self, user: int, guild: int = GUILD) -> Any:
        return SimpleNamespace(
            user=SimpleNamespace(id=user),
            guild=SimpleNamespace(id=guild),
            client=self.bot,
            response=SimpleNamespace(
                is_done=lambda: True, defer=AsyncMock(), send_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    @staticmethod
    def said(it: Any) -> str:
        return str(it.followup.send.await_args.args[0])

    async def current(self) -> Any:
        return await self.store.get(GUILD, self.campaign.id, self.picture.id)

    async def test_the_poster_and_a_dm_make_it_private(self) -> None:
        for user in (POSTER, DM):
            await self.store.set_public(GUILD, self.campaign.id, self.picture.id, True)
            it = self.press(user)
            await kept_pictures.PrivateButton(self.picture.id).callback(it)
            self.assertFalse((await self.current()).public)
            self.assertEqual(self.said(it), kept_pictures.SAID["private"])

    async def test_a_stranger_cannot_make_it_private(self) -> None:
        it = self.press(STRANGER)
        await kept_pictures.PrivateButton(self.picture.id).callback(it)
        self.assertTrue((await self.current()).public)
        self.assertEqual(self.said(it), kept_pictures.NOT_YOURS)

    async def test_delete_asks_once_and_deletes_nothing_yet(self) -> None:
        it = self.press(POSTER)
        await kept_pictures.DeleteButton(self.picture.id).callback(it)
        self.assertEqual(self.said(it), kept_pictures.SURE)
        self.assertIsNone((await self.current()).deleted_at)
        self.assertEqual(self.bucket.deleted, [])
        view = it.followup.send.await_args.kwargs["view"]
        self.assertEqual(
            [i.item.custom_id for i in view.children], [f"dmbot:picdely:{self.picture.id}"]
        )

    async def test_the_poster_and_a_dm_delete_it_after_the_question(self) -> None:
        it = self.press(DM)
        await kept_pictures.DeleteYesButton(self.picture.id).callback(it)
        self.assertEqual(self.said(it), kept_pictures.SAID["delete"])
        self.assertIsNotNone((await self.current()).deleted_at)
        self.assertEqual(sorted(self.bucket.deleted), ["p/one-preview.webp", "p/one.png"])

    async def test_a_stranger_is_not_even_asked_and_cannot_confirm(self) -> None:
        asked = self.press(STRANGER)
        await kept_pictures.DeleteButton(self.picture.id).callback(asked)
        self.assertEqual(self.said(asked), kept_pictures.NOT_YOURS)
        self.assertNotIn("view", asked.followup.send.await_args.kwargs)
        # (a forwarded question: the confirmation checks the person again)
        forwarded = self.press(STRANGER)
        await kept_pictures.DeleteYesButton(self.picture.id).callback(forwarded)
        self.assertEqual(self.said(forwarded), kept_pictures.NOT_YOURS)
        self.assertIsNone((await self.current()).deleted_at)
        self.assertEqual(self.bucket.deleted, [])

    async def test_a_picture_already_gone_says_so(self) -> None:
        await self.store.delete(GUILD, self.campaign.id, self.picture.id)
        for button in (
            kept_pictures.PrivateButton(self.picture.id),
            kept_pictures.DeleteButton(self.picture.id),
            kept_pictures.DeleteYesButton(self.picture.id),
        ):
            it = self.press(DM)
            await button.callback(it)
            self.assertEqual(self.said(it), GONE)

    async def test_another_servers_number_finds_nothing(self) -> None:
        it = self.press(DM, guild=OTHER)
        await kept_pictures.PrivateButton(self.picture.id).callback(it)
        self.assertEqual(self.said(it), GONE)
        self.assertTrue((await self.current()).public)

    async def test_a_failing_store_gets_a_plain_answer_not_silence(self) -> None:
        self.bot.picture_actions = SimpleNamespace(apply=AsyncMock(side_effect=RuntimeError("x")))
        it = self.press(POSTER)
        with self.assertLogs("dmbot.ui.kept_pictures", "ERROR"):
            await kept_pictures.PrivateButton(self.picture.id).callback(it)
        self.assertEqual(self.said(it), kept_pictures.FAILED)
        self.bot.picture_store = SimpleNamespace(by_id=AsyncMock(side_effect=RuntimeError("x")))
        asked = self.press(POSTER)
        with self.assertLogs("dmbot.ui.kept_pictures", "ERROR"):
            await kept_pictures.DeleteButton(self.picture.id).callback(asked)
        self.assertEqual(self.said(asked), kept_pictures.FAILED)

    async def test_outside_a_server_nothing_happens(self) -> None:
        it = self.press(POSTER)
        it.guild = None
        for button in (kept_pictures.PrivateButton(1), kept_pictures.DeleteButton(1)):
            await button.callback(it)
            self.assertEqual(self.said(it), kept_pictures.NOT_HERE)

    async def test_the_poster_of_one_picture_cannot_touch_anothers(self) -> None:
        added = await self.store.add(
            GUILD,
            self.campaign.id,
            poster_id=STRANGER,
            session_id="",
            line_id=None,
            message_id=2,
            description="a blue door",
            consent_version=7,
            right_granted_at=900,
            sha256="b" * 64,
            object_key="p/two.png",
            preview_key="",
            size_bytes=MB,
            preview_bytes=0,
            storage_mb=5,
        )
        assert added.picture is not None
        it = self.press(POSTER)
        await kept_pictures.DeleteYesButton(added.picture.id).callback(it)
        self.assertEqual(self.said(it), kept_pictures.NOT_YOURS)
        other = await self.store.get(GUILD, self.campaign.id, added.picture.id)
        assert other is not None
        self.assertIsNone(other.deleted_at)

    async def test_when_pictures_are_off_it_says_so(self) -> None:
        self.bot.picture_actions = None
        it = self.press(POSTER)
        await kept_pictures.PrivateButton(self.picture.id).callback(it)
        self.assertEqual(self.said(it), kept_pictures.OFF)


def test_ids_fit_discord_and_the_view_has_both_buttons() -> None:
    view = kept_pictures.link_view(10**17)
    ids = [cast(Any, i).item.custom_id for i in view.children]  # (a DynamicItem wraps a button)
    assert ids == ["dmbot:picpriv:100000000000000000", "dmbot:picdel:100000000000000000"]
    assert all(len(i) <= 100 for i in ids)
