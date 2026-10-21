"""A short session asks the DM before a recap is written (#2104)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.recaps import short
from dmbot.transcript.models import Line
from dmbot.ui import recap_short as screen

GUILD = 11
CID = "c" * 32
AT = 100
ALICE, BOB, DM = 7, 8, 5
MIN = 60_000


def said(start_min: int, minutes: int, user: int = ALICE, **kw: Any) -> Line:
    return Line(start_min * MIN, user, "x", "words said", duration_ms=minutes * MIN, **kw)


class TheNumbers(unittest.TestCase):
    def test_a_twenty_minute_session_is_short(self) -> None:
        lines = [said(0, 10), said(10, 10)]
        self.assertEqual(short.span_minutes(lines), 20)
        self.assertTrue(short.is_short(20, short.talk_minutes(lines, {ALICE})))

    def test_two_hours_with_ten_minutes_of_game_talk_is_short(self) -> None:
        lines = [said(0, 10), said(110, 10, topic="off_topic")]
        lines += [said(20, 90, BOB, topic="off_topic")]
        self.assertGreaterEqual(short.span_minutes(lines), 110)
        talk = short.talk_minutes(lines, {ALICE, BOB})
        self.assertEqual(talk, 10)  # the chat that was left out does not count
        self.assertTrue(short.is_short(short.span_minutes(lines), talk))

    def test_two_hours_with_an_hour_of_game_talk_is_not_short(self) -> None:
        lines = [said(i * 2, 1) for i in range(60)] + [said(119, 1)]
        self.assertFalse(
            short.is_short(short.span_minutes(lines), short.talk_minutes(lines, {ALICE}))
        )

    def test_only_people_who_agreed_and_no_sidebar_count(self) -> None:
        lines = [said(0, 40), said(40, 40, BOB), said(80, 40, sidebar="question")]
        self.assertEqual(short.talk_minutes(lines, {ALICE}), 40)

    def test_the_question_is_plain_and_says_what_no_answer_means(self) -> None:
        self.assertEqual(
            short.question(22, 9),
            "Short session: 22 minutes long, 9 minutes of game talk (off-topic chat not "
            "counted). Write a recap?\nNo answer means no recap.",
        )
        self.assertIn("1 minute long", short.question(1, 0))
        self.assertIn("under a minute of game talk", short.question(1, 0))


def campaign() -> Any:
    return SimpleNamespace(
        guild_id=GUILD, id=CID, recaps_text=True, recaps_email=True, owner_user_id=DM,
        dm_user_ids=frozenset({DM}),
    )  # fmt: skip


def fake_bot(lines: list[Line], *, newer: int | None = None) -> Any:
    async def plan_allows(*_: Any, **__: Any) -> bool:
        return True

    newest = [SimpleNamespace(id="s", started_at=newer or AT)]
    bot = SimpleNamespace(
        settings=SimpleNamespace(recaps_on=True, site_url="https://site.example.com"),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        plan_allows=plan_allows,
        recap_worker=object(),
        recaps=SimpleNamespace(
            enqueue=AsyncMock(return_value=True), get=AsyncMock(return_value=None)
        ),
        transcripts=SimpleNamespace(
            session=AsyncMock(
                return_value=SimpleNamespace(id="s", started_at=AT, lines=len(lines))
            ),
            lines=AsyncMock(return_value=lines),
            sessions=AsyncMock(return_value=newest),
        ),
        consent=SimpleNamespace(consenting=AsyncMock(return_value={ALICE})),
        post=AsyncMock(),
        post_message=AsyncMock(return_value=SimpleNamespace(id=1, content="q")),
        decision_lines=MagicMock(close=AsyncMock(), remember_pressed=MagicMock()),
        _short_asks={},
        _writing_said={},
        recap_holds=None,
    )
    for name in (
        "recap_session", "_queue_recap", "recap_allowed", "recap_short_expired",
        "recap_write_anyway", "_expire_short_asks", "_hold_or_queue", "_say_writing",
    ):  # fmt: skip
        setattr(bot, name, getattr(DMBot, name).__get__(bot))
    return bot


def table() -> Any:
    return SimpleNamespace(
        campaign_id=CID, guild_id=GUILD, transcript_session_id="s", screen_channel_id=66
    )


class WhenTheSessionEnds(unittest.IsolatedAsyncioTestCase):
    async def test_a_short_session_asks_and_queues_nothing(self) -> None:
        bot = fake_bot([said(0, 10), said(10, 10)])
        await bot.recap_session(table())
        bot.recaps.enqueue.assert_not_awaited()
        ((channel, text, view), kwargs) = (
            bot.post_message.await_args.args,
            bot.post_message.await_args.kwargs,
        )
        self.assertEqual((channel, text), (66, short.question(20, 20)))
        self.assertEqual(
            [cast(Any, c).custom_id for c in view.children],
            [f"dmbot:recapask:go:{CID}:{AT}", f"dmbot:recapask:skip:{CID}:{AT}"],
        )
        self.assertEqual(kwargs["remember"], screen.ask_key(CID, AT))
        self.assertEqual(bot._short_asks, {CID: AT})

    async def test_a_session_of_two_hours_with_an_hour_of_talk_is_queued_as_usual(self) -> None:
        bot = fake_bot([said(i * 2, 1) for i in range(60)] + [said(119, 1)])
        await bot.recap_session(table())
        bot.recaps.enqueue.assert_awaited_once()
        bot.post_message.assert_not_awaited()

    async def test_asking_twice_is_asking_once(self) -> None:
        bot = fake_bot([said(0, 10)])
        bot.recaps.get = AsyncMock(return_value=SimpleNamespace())  # a job (or decision) exists
        await bot.recap_session(table())
        bot.post_message.assert_not_awaited()

    async def test_a_question_that_could_not_be_posted_is_not_remembered(self) -> None:
        bot = fake_bot([said(0, 10)])
        bot.post_message = AsyncMock(return_value=None)
        await bot.recap_session(table())
        self.assertEqual(bot._short_asks, {})

    def test_the_start_of_a_session_expires_the_question(self) -> None:
        import inspect

        source = inspect.getsource(DMBot)
        self.assertIn('self._track(self._expire_short_asks(table), "expire-short-asks")', source)

    async def test_the_next_session_expires_the_open_question(self) -> None:
        bot = fake_bot([said(0, 10)])
        await bot.recap_session(table())
        await bot._expire_short_asks(table())
        bot.decision_lines.close.assert_awaited_once_with(screen.ask_key(CID, AT), screen.EXPIRED)
        self.assertEqual(bot._short_asks, {})


def press_on(bot: Any, act: str, user: int = DM) -> tuple[Any, Any]:
    button = screen.ShortRecapButton(act, CID, AT)
    done = {"yes": False}
    message = SimpleNamespace(id=9, content="This session was short")
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=user),
        channel_id=66,
        message=message,
        response=SimpleNamespace(
            is_done=lambda: done["yes"],
            send_message=AsyncMock(),
            defer=AsyncMock(side_effect=lambda: done.update(yes=True)),
        ),
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return button, interaction


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_write_a_recap_queues_it_and_says_so(self) -> None:
        bot = fake_bot([said(0, 10)])
        button, i = press_on(bot, "go")
        await button.callback(i)
        bot.recaps.enqueue.assert_awaited_once_with(GUILD, CID, AT, dm_user_id=DM)
        self.assertIn("Writing a recap", bot.post.await_args.args[1])
        self.assertEqual(bot.post.await_args.args[0], 66)
        self.assertIsNone(i.edit_original_response.await_args.kwargs["view"])
        self.assertIn("Writing the recap.", i.edit_original_response.await_args.kwargs["content"])

    async def test_no_recap_writes_nothing(self) -> None:
        bot = fake_bot([said(0, 10)])
        button, i = press_on(bot, "skip")
        await button.callback(i)
        bot.recaps.enqueue.assert_not_awaited()
        bot.post.assert_not_awaited()
        self.assertIn("No recap written.", i.edit_original_response.await_args.kwargs["content"])

    async def test_a_player_who_is_not_a_dm_is_refused(self) -> None:
        bot = fake_bot([said(0, 10)])
        for act in ("go", "skip"):
            button, i = press_on(bot, act, user=BOB)
            await button.callback(i)
            bot.recaps.enqueue.assert_not_awaited()
            i.edit_original_response.assert_not_awaited()
            self.assertEqual(i.response.send_message.await_args.args[0], screen.NOT_YOURS)

    async def test_a_press_after_a_newer_session_says_expired(self) -> None:
        bot = fake_bot([said(0, 10)], newer=AT + 500)
        button, i = press_on(bot, "go")
        await button.callback(i)
        bot.recaps.enqueue.assert_not_awaited()
        self.assertIn(
            "Expired: a newer session started.",
            i.edit_original_response.await_args.kwargs["content"],
        )

    async def test_a_plan_that_lost_recaps_refuses_politely(self) -> None:
        bot = fake_bot([said(0, 10)])

        async def no(*_: Any, **__: Any) -> bool:
            return False

        bot.plan_allows = no
        button, i = press_on(bot, "go")
        await button.callback(i)
        bot.recaps.enqueue.assert_not_awaited()
        self.assertEqual(i.followup.send.await_args.args[0], screen.NOT_NOW)
        i.edit_original_response.assert_not_awaited()  # the buttons stay on

    async def test_a_second_press_when_it_is_already_queued_says_so(self) -> None:
        bot = fake_bot([said(0, 10)])
        bot.recaps.enqueue = AsyncMock(return_value=False)
        button, i = press_on(bot, "go")
        await button.callback(i)
        self.assertIn(screen.ALREADY, i.edit_original_response.await_args.kwargs["content"])
        bot.post.assert_not_awaited()

    async def test_pressing_write_a_recap_waits_for_the_name_answers_like_any_session(self) -> None:
        bot = fake_bot([said(0, 10)])
        bot.recap_write_anyway = AsyncMock(return_value="held")
        button, i = press_on(bot, "go")
        await button.callback(i)
        self.assertIn(screen.HELD, i.edit_original_response.await_args.kwargs["content"])
        self.assertEqual(screen.HELD, "Waiting for your answers about names.")


if __name__ == "__main__":
    unittest.main()
