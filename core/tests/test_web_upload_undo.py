"""The website's "Undo this upload" (#3879): a press is queued for the bot through its own database
door and read back by polling. Only a DM of the campaign can press it; the upload must have a
notice that can still be undone (not taken back, changed something, under seven days); a second
press is given the first one's id; a press that stopped moving is lost. Real Postgres; invented
names."""

from __future__ import annotations

import time
from typing import Any

from dmbot.upload_notices import UNDO_SECONDS, Notice, UploadNotices
from dmbot.web import upload_notice
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_theatre_script import Script


class UploadUndo(Script):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.notices = UploadNotices(self.db)

    async def add(self, source_id: str = "up1", changes: list[int] | None = None) -> Notice:
        return await self.notices.add(
            THURSDAY.id, self.one.id, "The Sunken Keep", {"npc": 3},
            [1, 2, 3] if changes is None else changes,
            source_kind="lore", source_id=source_id,
        )  # fmt: skip

    async def press(
        self, source_id: str = "up1", kind: object = "lore", who: Session | None = None
    ) -> str:
        return await upload_notice.undo(self.web, who or self.session, self.cref, kind, source_id)

    async def refusal(self, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.press(**kw)
        return caught.exception.code

    async def test_an_undo_is_queued_and_read_back_waiting(self) -> None:
        await self.add()
        made = await self.press()
        got = await upload_notice.read_undo(self.web, self.session, self.cref, made)
        self.assertEqual((got["state"], got["undone"]), ("waiting", 0))
        self.assertEqual(got["progress"]["state"], "running")

    async def test_a_second_press_on_the_same_upload_is_given_the_first_id(self) -> None:
        await self.add()
        first = await self.press()
        self.assertEqual(await self.press(), first)  # (one undo at a time)

    async def test_an_upload_with_no_notice_is_not_found(self) -> None:
        await self.add("up1")
        self.assertEqual(await self.refusal(source_id="up2"), "not_found")

    async def test_an_upload_already_taken_back_or_that_changed_nothing_is_refused(self) -> None:
        made = await self.add()
        assert await self.notices.claim_undo(THURSDAY.id, made.id) is not None
        self.assertEqual(await self.refusal(), "undone")
        await self.add("up2", changes=[])
        self.assertEqual(await self.refusal(source_id="up2"), "undone")

    async def test_the_window_closes_after_seven_days(self) -> None:
        made = await self.add()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE upload_notices SET created_at = %s WHERE id = %s",
                (made.created_at - UNDO_SECONDS - 5, made.id),
            )
        self.assertEqual(await self.refusal(), "too_late")

    async def test_a_wrong_shape_is_refused_before_the_door(self) -> None:
        await self.add()
        self.assertEqual(await self.refusal(kind="other"), "bad_value")
        self.assertEqual(await self.refusal(kind=7), "bad_value")
        self.assertEqual(await self.refusal(source_id="x y"), "bad_value")

    async def test_a_player_cannot_press_or_read_an_undo(self) -> None:
        await self.add()
        bob = await self.signed_in(BOB)  # in the server, not a DM of the campaign
        self.assertEqual(await self.refusal(who=bob), "not_found")
        made = await self.press()
        with self.assertRaises(RecapError) as caught:
            await upload_notice.read_undo(self.web, bob, self.cref, made)
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as other:  # a campaign this person is no DM of
            await upload_notice.undo(
                self.web, self.session, f"{THURSDAY.id}.{self.two.id}", "lore", "up1"
            )
        self.assertEqual(other.exception.code, "not_found")

    async def test_the_undo_table_is_not_open_to_the_website_role(self) -> None:
        await self.add()
        await self.press()
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM upload_undos")

    async def test_a_press_that_stopped_moving_is_lost_and_can_be_pressed_again(self) -> None:
        await self.add()
        first = await self.press()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE upload_undos SET created_at = created_at - 3600 WHERE id = %s", (first,)
            )
        self.assertNotEqual(await self.press(), first)

    async def test_a_press_that_is_still_moving_is_not_lost(self) -> None:
        await self.add()
        first = await self.press()
        old = int(time.time()) - 3600  # queued an hour ago, but the bot moved a minute ago
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE upload_undos SET state = 'working', created_at = %s, claimed_at = %s"
                " WHERE id = %s",
                (old, (int(time.time()) - 60) * 1000, first),
            )
        self.assertEqual(await self.press(), first)

    async def test_a_failed_press_lets_the_upload_be_undone_again(self) -> None:
        await self.add()
        first = await self.press()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE upload_undos SET state = 'failed', error = 'failed' WHERE id = %s",
                (first,),
            )
        got = await upload_notice.read_undo(self.web, self.session, self.cref, first)
        self.assertEqual((got["state"], got["progress"]["state"]), ("failed", "failed"))
        self.assertNotEqual(await self.press(), first)

    async def test_the_limit_is_twenty_presses_a_minute(self) -> None:
        made = await self.add()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO upload_undos (guild_id, campaign_id, id, user_id, notice_id, state,"
                " created_at) SELECT %s, %s, md5(random()::text || n::text), 1, %s, 'done', %s"
                " FROM generate_series(1, 20) n",
                (THURSDAY.id, self.one.id, made.id, int(time.time())),
            )
        self.assertEqual(await self.refusal(), "too_soon")
