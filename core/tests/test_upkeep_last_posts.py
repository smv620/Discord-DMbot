"""The last post-game notices go where upkeep goes (#3874): the DM Bot channel, counts and fixed
words only (it is read by the campaign's people), else the DM screen as before. Invented ids."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot.recaps import uploaded
from dmbot.recaps.discord import RecapFeatures
from dmbot.ui import recap_audio

SCREEN, BOT = 111, 222
TITLE = "The Silver Lock"


def fake(*, dm_bot: int | None) -> Any:
    campaign = SimpleNamespace(
        guild_id=5, id="c" * 32, dm_screen_channel_id=SCREEN, dm_bot_channel_id=dm_bot
    )
    me = SimpleNamespace(
        post=AsyncMock(),
        post_message=AsyncMock(return_value=None),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        recaps=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(title=TITLE))),
        audio_waits=SimpleNamespace(finish=AsyncMock()),
        _audio_seen={},
        settings=SimpleNamespace(site_url=""),
        _recap_refusal=AsyncMock(return_value="you've used this month's recaps; next on 3 Dec."),
    )
    me._upkeep_channel = lambda c: RecapFeatures._upkeep_channel(cast(Any, me), c)
    return me, campaign


class TheNotices(unittest.IsolatedAsyncioTestCase):
    async def test_the_audio_notice_goes_to_the_dm_bot_channel_with_no_title(self) -> None:
        me, _ = fake(dm_bot=BOT)
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "ready")
        channel, text = me.post.await_args.args[:2]
        self.assertEqual(channel, BOT)
        self.assertNotIn(TITLE, text)
        self.assertIn("this session's recap", text)

    async def test_with_no_dm_bot_channel_the_dm_screen_gets_it_with_the_title(self) -> None:
        me, _ = fake(dm_bot=None)
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "failed")
        channel, text = me.post.await_args.args[:2]
        self.assertEqual(channel, SCREEN)
        self.assertIn(f"**{TITLE}**", text)

    async def test_the_allowance_refusal_is_a_fixed_line_in_the_dm_bot_channel(self) -> None:
        me, _ = fake(dm_bot=BOT)
        got = await RecapFeatures._recap_allowance(me, 5, "c" * 32, audio=False)
        self.assertIn("next on", got or "")  # (the caller still gets the full words)
        channel, text = me.post.await_args.args
        self.assertEqual(channel, BOT)
        self.assertNotIn("3 Dec", text)

    async def test_the_allowance_refusal_has_the_full_words_on_the_dm_screen(self) -> None:
        me, _ = fake(dm_bot=None)
        await RecapFeatures._recap_allowance(me, 5, "c" * 32, audio=False)
        channel, text = me.post.await_args.args
        self.assertEqual(channel, SCREEN)
        self.assertIn("3 Dec", text)

    async def test_writing_a_recap_and_a_group_of_them_go_to_the_dm_bot_channel(self) -> None:
        me, campaign = fake(dm_bot=BOT)
        campaign.recaps_email = False
        me.recaps.waiting_for_script = AsyncMock(return_value=False)
        await RecapFeatures._say_writing(me, campaign, SCREEN, 100, note="Waiting its turn.")
        self.assertEqual(me.post.await_args.args[0], BOT)
        await RecapFeatures._say_group_writing(me, campaign, SCREEN, 3)
        self.assertEqual(me.post.await_args.args[0], BOT)
        me2, campaign2 = fake(dm_bot=None)
        campaign2.recaps_email = False
        me2.recaps.waiting_for_script = AsyncMock(return_value=False)
        await RecapFeatures._say_writing(me2, campaign2, SCREEN, 100)
        await RecapFeatures._say_group_writing(me2, campaign2, SCREEN, 3)
        self.assertEqual([c.args[0] for c in me2.post.await_args_list], [SCREEN, SCREEN])

    async def test_without_a_dm_bot_channel_the_callers_own_channel_is_kept(self) -> None:
        me, campaign = fake(dm_bot=None)
        campaign.recaps_email = False
        me.recaps.waiting_for_script = AsyncMock(return_value=False)
        await RecapFeatures._say_writing(me, campaign, 999, 100)  # (a hold's stored channel)
        await RecapFeatures._say_group_writing(me, campaign, 999, 2)
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [999, 999])

    async def test_no_title_text_says_this_session_s_recap_and_a_title_is_in_bold(self) -> None:
        for make in (recap_audio.ready_text, recap_audio.failed_text):
            self.assertIn("this session's recap", make(""))
            self.assertNotIn("**", make(""))
            self.assertIn("**A {b} \\*c\\***", make("A {b} *c*"))  # (escaped, in bold)

    async def test_nothing_is_posted_when_neither_channel_is_set(self) -> None:
        me, campaign = fake(dm_bot=None)
        campaign.dm_screen_channel_id = None
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "ready")
        await RecapFeatures._recap_allowance(me, 5, "c" * 32, audio=False)
        await RecapFeatures.recap_audio_started(me, campaign, 100)
        me.post.assert_not_awaited()
        me.post_message.assert_not_awaited()

    async def test_making_the_audio_is_said_in_the_same_place(self) -> None:
        me, campaign = fake(dm_bot=BOT)
        me.audio_waits = _Waits()
        await RecapFeatures.recap_audio_started(me, campaign, 100)
        self.assertEqual(me.post_message.await_args.args[0], BOT)
        self.assertEqual(me.post_message.await_args.args[1], recap_audio.MAKING)


def uploaded_stand_in(*, dm_bot: int | None) -> Any:
    """The stand-in for the uploaded-recap check: a recap a DM wrote, with no check yet."""
    me, campaign = fake(dm_bot=dm_bot)
    job = SimpleNamespace(title=TITLE, summary="text", written_by="Mira Vale", checked_at=None)
    me.recaps = SimpleNamespace(
        get=AsyncMock(return_value=job), set_upload_check=AsyncMock(return_value=True)
    )
    me.recap_secret_names = AsyncMock(return_value=["Sire"])
    me.recap_allowed_names = AsyncMock(return_value=set())
    return me, campaign


class TheUploadedNotice(unittest.IsolatedAsyncioTestCase):
    async def test_the_added_recap_notice_in_the_dm_bot_channel_has_no_writer_and_no_secret_note(
        self,
    ) -> None:
        me, _ = uploaded_stand_in(dm_bot=BOT)
        # (The stand-in text names no secret, so the real note would be "": the patch makes the
        # 🤫 assertion below mean something.)
        with (
            patch.object(uploaded, "unknown_names", return_value=[]),
            patch.object(uploaded, "secret_note", return_value="The text names Sire."),
        ):
            self.assertTrue(await RecapFeatures.check_uploaded_recap(me, 5, "c" * 32, 100))
        kwargs = me.post.await_args.kwargs
        channel, text = me.post.await_args.args[:2]
        self.assertEqual(channel, BOT)
        self.assertIsNotNone(kwargs.get("view"))
        self.assertNotIn("Mira Vale", text)
        self.assertNotIn("🤫", text)
        self.assertIn("A recap was added for this session", text)
        self.assertIn("Nothing goes to the table until you say so.", text)

    async def test_the_added_recap_notice_on_the_dm_screen_keeps_the_writer_and_the_note(
        self,
    ) -> None:
        me, _ = uploaded_stand_in(dm_bot=None)
        with (
            patch.object(uploaded, "unknown_names", return_value=[]),
            patch.object(uploaded, "secret_note", return_value="The text names Sire."),
        ):
            await RecapFeatures.check_uploaded_recap(me, 5, "c" * 32, 100)
        channel, text = me.post.await_args.args[:2]
        self.assertEqual(channel, SCREEN)
        self.assertIn("**Mira Vale**", text)
        self.assertIn("🤫", text)


class OneChannelOnly(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_dm_bot_channel_set_still_gets_every_notice(self) -> None:
        me, campaign = fake(dm_bot=BOT)
        campaign.dm_screen_channel_id = None
        campaign.recaps_email = False
        me.recaps.waiting_for_script = AsyncMock(return_value=False)
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "ready")
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [BOT])
        await RecapFeatures._say_writing(me, campaign, 999, 100)
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [BOT, BOT])
        await RecapFeatures._recap_allowance(me, 5, "c" * 32, audio=False)
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [BOT, BOT, BOT])

    async def test_only_the_dm_screen_set_gets_every_notice_as_before(self) -> None:
        me, campaign = fake(dm_bot=None)
        campaign.recaps_email = False
        me.recaps.waiting_for_script = AsyncMock(return_value=False)
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "ready")
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [SCREEN])
        await RecapFeatures._say_writing(me, campaign, SCREEN, 100)
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [SCREEN, SCREEN])
        await RecapFeatures._recap_allowance(me, 5, "c" * 32, audio=False)
        self.assertEqual([c.args[0] for c in me.post.await_args_list], [SCREEN, SCREEN, SCREEN])


class _Waits:
    def __contains__(self, _key: object) -> bool:
        return False

    async def start(self, *_a: object) -> None:
        return None
