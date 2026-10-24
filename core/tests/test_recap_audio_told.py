"""The end of a recap's audio is said once on the DM screen (#2621): the database marker that makes
it once-only (cleared whenever the audio is queued, by any door), the bot's notice, and the three
buttons (Listen here, Keep it, Make the audio again), which only the campaign's DMs may press."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.recaps.approval import text_hash
from dmbot.ui import recap_audio
from dmbot.ui.recap_dm import NOT_YOURS
from tests import recap_fixtures as fx
from tests.test_recap_jobs import Base

GUILD, DM_ID, PLAYER = 5, 7, 11
CID = "c" * 32
AT = 100


class TheMarker(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, 100, state="ready", title="The Silver Lock", summary="Mira rose.",
            speakers=[fx.DM], game_day=4,
        )  # fmt: skip
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertTrue(
            await self.store.approve(
                1, self.one.id, 100, fx.DM, "both", expected=text_hash(job.title, job.summary)
            )
        )

    async def finish(self, audio: str) -> None:
        await self.store.set_audio(1, self.one.id, 100, audio=audio)

    async def test_nothing_is_told_while_the_audio_is_still_being_made(self) -> None:
        self.assertEqual(await self.store.audio_to_tell(1), [])

    async def test_ready_is_told_once_and_a_new_making_is_told_again(self) -> None:
        await self.finish("ready")
        self.assertEqual(await self.store.audio_to_tell(1), [(self.one.id, 100, "ready")])
        self.assertTrue(await self.store.mark_audio_told(1, self.one.id, 100, "ready"))
        self.assertEqual(await self.store.audio_to_tell(1), [])  # once
        await self.finish("queued")  # made again, by any door
        await self.finish("failed")
        self.assertEqual(await self.store.audio_to_tell(1), [(self.one.id, 100, "failed")])

    async def test_a_notice_for_audio_that_has_moved_on_marks_nothing(self) -> None:
        await self.finish("ready")
        await self.finish("queued")
        self.assertFalse(await self.store.mark_audio_told(1, self.one.id, 100, "ready"))
        await self.finish("ready")
        self.assertEqual(len(await self.store.audio_to_tell(1)), 1)  # still owed

    async def test_make_it_again_follows_the_websites_rules(self) -> None:
        self.assertEqual(await self.store.audio_again(1, self.one.id, 100), "not_allowed")  # queued
        await self.finish("failed")
        self.assertEqual(await self.store.audio_again(1, self.one.id, 100), "queued")
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_tries), ("queued", 0))
        await self.finish("failed")
        self.assertEqual(await self.store.audio_again(1, self.one.id, 100), "too_soon")
        self.clock.now += 601
        self.assertEqual(await self.store.audio_again(1, self.one.id, 100), "queued")

    async def test_the_notice_is_not_made_for_a_skipped_recap(self) -> None:
        await self.finish("ready")
        await self.store.decide(1, self.one.id, 100, "drop")
        self.assertEqual(await self.store.audio_to_tell(1), [])


def press(button: Any, user: int = DM_ID, *, files: Any = None) -> tuple[Any, Any, Any]:
    campaign = SimpleNamespace(guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}))
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        recaps=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(title="T")),
            audio_again=AsyncMock(return_value="queued"),
        ),
        recap_audio_started=AsyncMock(),
        _recap_audio_files=AsyncMock(return_value=files or ([], "")),
    )
    response = SimpleNamespace(
        is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        channel=object(),
        user=SimpleNamespace(id=user),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(content="line", id=1),
    )
    return button, bot, interaction


def _label(child: Any) -> str:
    return str(getattr(child, "label", None) or child.item.label)


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_dm_may_press_any_of_them(self) -> None:
        for cls in (
            recap_audio.ListenButton,
            recap_audio.KeepAudioButton,
            recap_audio.MakeAudioAgainButton,
        ):
            button, bot, interaction = press(cls(CID, AT), PLAYER)
            await button.callback(interaction)
            self.assertEqual(interaction.response.send_message.await_args.args[0], NOT_YOURS)
            bot.recaps.audio_again.assert_not_awaited()
            bot._recap_audio_files.assert_not_awaited()
            interaction.followup.send.assert_not_awaited()

    async def test_listen_sends_the_files_privately_to_the_dm(self) -> None:
        f = MagicMock()
        button, _bot, interaction = press(recap_audio.ListenButton(CID, AT), files=([f], ""))
        await button.callback(interaction)
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["files"], [f])
        f.close.assert_called_once()

    async def test_listen_with_no_files_points_to_the_page(self) -> None:
        button, _bot, interaction = press(recap_audio.ListenButton(CID, AT))
        await button.callback(interaction)
        self.assertEqual(interaction.response.send_message.await_args.args[0], recap_audio.NO_FILES)

    async def test_keep_closes_the_line_and_posts_nothing(self) -> None:
        button, _bot, interaction = press(recap_audio.KeepAudioButton(CID, AT))
        await button.callback(interaction)
        edit = interaction.response.edit_message.await_args.kwargs
        self.assertIsNone(edit["view"])
        self.assertIn(recap_audio.KEPT_LINE, edit["content"])

    async def test_make_again_says_what_came_of_it(self) -> None:
        for outcome in ("queued", "too_soon", "not_allowed"):
            button, bot, interaction = press(recap_audio.MakeAudioAgainButton(CID, AT))
            bot.recaps.audio_again.return_value = outcome
            await button.callback(interaction)
            self.assertEqual(
                interaction.response.send_message.await_args.args[0],
                recap_audio.AGAIN_WORDS[outcome],
            )

    async def test_the_views_carry_the_right_buttons(self) -> None:
        ready = recap_audio.audio_view(CID, AT, failed=False, review_url="https://x.example.com/a")
        labels = [_label(c) for c in ready.children]
        self.assertEqual(
            labels, ["Listen here", "Review", "Post to the table", "Keep it, don't post"]
        )
        failed = recap_audio.audio_view(CID, AT, failed=True, review_url="https://x.example.com/a")
        labels = [_label(c) for c in failed.children]
        self.assertEqual(labels, ["Review", "Make the audio again", "Post to the table"])


class TheWaitingLine(unittest.IsolatedAsyncioTestCase):
    """The "Making the audio" line is kept current like every wait, then closed."""

    async def asyncSetUp(self) -> None:
        self.now = 1000.0
        self.waits = recap_audio.AudioWaits(clock=lambda: self.now)
        self.message = SimpleNamespace(edit=AsyncMock())
        self.key = (GUILD, CID, AT)

    def last_text(self) -> str:
        return str(self.message.edit.await_args.kwargs["content"])

    async def test_it_shows_the_step_the_time_so_far_and_the_last_change(self) -> None:
        await self.waits.start(self.key, self.message)
        waiting = self.waits._open[self.key][1]
        self.now += 100
        await waiting._refresh()
        self.assertIn(recap_audio.MAKING_STEP, self.last_text())
        self.assertIn("1 min 40 s so far", self.last_text())
        self.now += 20
        self.waits.moved(self.key)  # a try began: that is a change
        self.now += 90
        await waiting._refresh()
        self.assertIn("Last change 1 min ago", self.last_text())
        await self.waits.finish(self.key, "x")

    async def test_finishing_leaves_a_last_word_not_a_making_line(self) -> None:
        await self.waits.start(self.key, self.message)
        await self.waits.finish(self.key, recap_audio.DONE_LINE)
        self.assertEqual(self.last_text(), recap_audio.DONE_LINE)
        self.assertNotIn("Making", self.last_text())
        self.assertNotIn(self.key, self.waits.open_keys())
        await self.waits.finish(self.key, "again")  # a second finish does nothing
        self.assertEqual(self.last_text(), recap_audio.DONE_LINE)

    async def test_a_stall_is_named_with_when_dmbot_stops(self) -> None:
        await self.waits.start(self.key, self.message)
        self.now += recap_audio.STALL_AFTER_S + 5
        await self.waits._open[self.key][1]._refresh()
        self.assertIn("No change for 10 min.", self.last_text())
        self.assertIn("DMbot will stop by itself at 20 min", self.last_text())
        await self.waits.finish(self.key, "x")
