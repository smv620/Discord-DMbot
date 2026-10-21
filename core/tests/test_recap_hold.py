"""A session's recap waits for the DM's name answers (#2169). Invented fixtures only."""

from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns.store import CampaignStore
from dmbot.memory.models import CONFIRMED, PROPOSED
from dmbot.recaps import hold
from dmbot.recaps.store import RecapStore
from dmbot.ui import recap_hold as screen
from tests.pg import DatabaseTest

GUILD = 5101
OTHER = 5102
DM = 7
AT = 1000


class TheText(IsolatedAsyncioTestCase):
    async def test_the_line_is_plain_and_counts(self) -> None:
        self.assertEqual(
            hold.question_text(7),
            "📝 7 name questions from tonight. Answer them and DMbot writes the recap.",
        )
        self.assertEqual(
            hold.question_text(1),
            "📝 1 name question from tonight. Answer them and DMbot writes the recap.",
        )


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 5000.0
        self.holds = hold.HoldStore(self.db, clock=lambda: self.now)
        self.recaps = RecapStore(self.db)
        self.campaign = await CampaignStore(self.db).create(GUILD, "One", DM)

    async def hold_it(self, at: int = AT) -> bool:
        return await self.holds.hold(
            GUILD, self.campaign.id, at, session_id="s1", dm_user_id=DM, channel_id=66
        )

    async def jobs(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM recap_jobs")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_a_hold_is_kept_once_and_listed(self) -> None:
        self.assertTrue(await self.hold_it())
        self.assertFalse(await self.hold_it())  # the end of the session repeated: one wait
        held = await self.holds.held(GUILD)
        self.assertEqual(
            [(h.campaign_id, h.session_at, h.session_id, h.dm_user_id, h.channel_id) for h in held],
            [(self.campaign.id, AT, "s1", DM, 66)],
        )
        self.assertEqual(await self.jobs(), 0)  # held: no job, so no cost

    async def test_nothing_is_held_for_a_session_that_has_a_recap_job(self) -> None:
        await self.recaps.enqueue(GUILD, self.campaign.id, AT, dm_user_id=DM)
        self.assertFalse(await self.hold_it())

    async def test_releasing_makes_the_job_once(self) -> None:
        await self.hold_it()
        self.assertEqual(await self.holds.release(GUILD, self.campaign.id, AT), hold.QUEUED)
        self.assertEqual(await self.jobs(), 1)
        self.assertEqual(await self.holds.release(GUILD, self.campaign.id, AT), hold.GONE)
        self.assertEqual(await self.jobs(), 1)  # a second answer writes nothing again
        self.assertEqual(await self.holds.held(GUILD), [])

    async def test_a_hold_whose_job_exists_is_dropped_without_a_second_job(self) -> None:
        await self.hold_it()
        await self.recaps.enqueue(GUILD, self.campaign.id, AT, dm_user_id=DM)
        self.assertEqual(await self.holds.release(GUILD, self.campaign.id, AT), hold.ALREADY)
        self.assertEqual(await self.jobs(), 1)

    async def test_dropping_a_hold_makes_no_job(self) -> None:
        await self.hold_it()
        await self.holds.release(GUILD, self.campaign.id, AT, queue=False)
        self.assertEqual(await self.jobs(), 0)
        self.assertEqual(await self.holds.held(GUILD), [])

    async def test_the_count_of_open_questions_is_kept_and_only_written_when_it_changes(
        self,
    ) -> None:
        await self.holds.hold(
            GUILD, self.campaign.id, AT, session_id="s1", dm_user_id=DM, channel_id=66, names_open=5
        )
        (held,) = await self.holds.held(GUILD)
        self.assertEqual(held.names_open, 5)
        await self.holds.set_names_open(GUILD, self.campaign.id, AT, 2)
        got = await self.holds.get(GUILD, self.campaign.id, AT)
        assert got is not None
        self.assertEqual(got.names_open, 2)
        await self.holds.set_names_open(GUILD, self.campaign.id, AT, -4)  # never below nothing
        got = await self.holds.get(GUILD, self.campaign.id, AT)
        assert got is not None
        self.assertEqual(got.names_open, 0)

    async def test_the_reminder_is_claimed_once_and_can_be_given_back(self) -> None:
        await self.hold_it()
        self.assertTrue(await self.holds.mark_reminded(GUILD, self.campaign.id, AT))
        self.assertFalse(await self.holds.mark_reminded(GUILD, self.campaign.id, AT))  # once
        (held,) = await self.holds.held(GUILD)
        self.assertTrue(held.reminded)
        await self.holds.unmark_reminded(GUILD, self.campaign.id, AT)
        got = await self.holds.get(GUILD, self.campaign.id, AT)
        assert got is not None
        self.assertFalse(got.reminded)

    async def test_a_real_hold_older_than_the_cap_is_released_by_a_round(self) -> None:
        await self.hold_it()
        self.now += hold.HOLD_MAX_S + 60  # three days pass, by the store's clock
        bot = fake_bot(holds=self.holds, proposed=2)
        real = SimpleNamespace(**{**vars(campaign()), "id": self.campaign.id})
        bot.campaigns.get = AsyncMock(return_value=real)
        (held,) = await self.holds.held(GUILD)
        await bot._release_one(self.holds, GUILD, held)
        self.assertEqual(await self.jobs(), 1)  # the recap was queued
        self.assertEqual(await self.holds.held(GUILD), [])
        self.assertIn(hold.EXPIRED_NOTE, bot.post.await_args.args[1])

    async def test_an_unchanged_count_writes_no_row(self) -> None:
        await self.holds.hold(
            GUILD, self.campaign.id, AT, session_id="s1", dm_user_id=DM, channel_id=66, names_open=4
        )
        self.assertFalse(await self.holds.set_names_open(GUILD, self.campaign.id, AT, 4))
        self.assertTrue(await self.holds.set_names_open(GUILD, self.campaign.id, AT, 0))
        self.assertFalse(await self.holds.set_names_open(GUILD, self.campaign.id, AT, 0))

    async def test_a_silent_hold_is_told_once(self) -> None:
        self.assertTrue(
            await self.holds.hold(
                GUILD,
                self.campaign.id,
                AT,
                session_id="s1",
                dm_user_id=DM,
                channel_id=66,
                asked=False,
            )
        )
        (silent,) = await self.holds.held(GUILD)
        self.assertFalse(silent.asked)
        self.assertTrue(await self.holds.mark_asked(GUILD, self.campaign.id, AT))
        self.assertFalse(await self.holds.mark_asked(GUILD, self.campaign.id, AT))
        (told,) = await self.holds.held(GUILD)
        self.assertTrue(told.asked)

    async def test_a_server_with_no_holds_is_not_asked_again_until_one_is_made(self) -> None:
        self.assertEqual(await self.holds.held(GUILD), [])
        async with self.db.guild(GUILD) as conn:  # a row made behind the store's back
            await conn.execute(
                "INSERT INTO recap_holds (guild_id, campaign_id, session_at, session_id,"
                " dm_user_id, channel_id, created_at) VALUES (%s, %s, 1, 's', 7, 1, 1)",
                (GUILD, self.campaign.id),
            )
        self.assertEqual(await self.holds.held(GUILD), [])  # remembered as empty: no query
        await self.hold_it()  # a hold made through the store clears the mark
        self.assertEqual(len(await self.holds.held(GUILD)), 2)

    async def test_a_hold_made_while_the_server_is_read_is_not_hidden(self) -> None:
        real = self.holds._read

        async def read_then_hold(guild_id: int) -> Any:
            rows = await real(guild_id)  # the read finds nothing ...
            await self.hold_it()  # ... and a hold commits before the read's mark is made
            return rows

        with patch.object(self.holds, "_read", read_then_hold):
            self.assertEqual(await self.holds.held(GUILD), [])  # this read did not see it
        self.assertEqual(len(await self.holds.held(GUILD)), 1)  # but the next one asks again

    async def test_two_releases_at_once_make_one_job(self) -> None:
        await self.hold_it()
        results = await asyncio.gather(
            *(self.holds.release(GUILD, self.campaign.id, AT) for _ in range(4))
        )
        self.assertEqual(sorted(results), [hold.GONE, hold.GONE, hold.GONE, hold.QUEUED])
        self.assertEqual(await self.jobs(), 1)

    async def test_holds_stay_in_their_own_server_and_campaign(self) -> None:
        await self.hold_it()
        other = await CampaignStore(self.db).create(OTHER, "Two", DM)
        await self.holds.hold(OTHER, other.id, AT, session_id="s9", dm_user_id=DM, channel_id=1)
        self.assertEqual(len(await self.holds.held(GUILD)), 1)
        self.assertEqual(len(await self.holds.held(OTHER)), 1)
        # Another server cannot release this one's.
        self.assertEqual(await self.holds.release(OTHER, self.campaign.id, AT), hold.GONE)
        self.assertEqual(len(await self.holds.held(GUILD)), 1)

    async def test_a_hold_goes_with_its_campaign(self) -> None:
        await self.hold_it()
        await CampaignStore(self.db).delete(GUILD, self.campaign.id)
        self.assertEqual(await self.holds.held(GUILD), [])


def entity(created_at: int, status: str = PROPOSED) -> Any:
    return SimpleNamespace(created_at=created_at, status=status)


def campaign() -> Any:
    return SimpleNamespace(
        guild_id=GUILD, id="c" * 32, recaps_text=True, recaps_email=True, owner_user_id=DM,
        dm_user_ids=frozenset({DM}),
    )  # fmt: skip


def fake_bot(
    *, proposed: int = 0, did_they_mean: int = 0, holds: Any = "default", extractor: bool = False
) -> Any:
    store = SimpleNamespace(
        hold=AsyncMock(return_value=True),
        held=AsyncMock(return_value=[]),
        release=AsyncMock(return_value=hold.QUEUED),
        mark_asked=AsyncMock(return_value=True),
        unmark_asked=AsyncMock(),
        now=lambda: time.time(),
        mark_reminded=AsyncMock(return_value=True),
        unmark_reminded=AsyncMock(),
        set_names_open=AsyncMock(),
        get=AsyncMock(return_value=hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()))),
    )
    bot = SimpleNamespace(
        settings=SimpleNamespace(site_url="", recaps_on=True, story_extractor_on=extractor),
        extractor_ai=object() if extractor else None,
        transcripts=SimpleNamespace(
            next_session_start=AsyncMock(return_value=AT + 10_000),
            session=AsyncMock(return_value=SimpleNamespace(id="s1", started_at=AT)),
            sessions=AsyncMock(return_value=[SimpleNamespace(id="s1", started_at=AT)]),
        ),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        memory=SimpleNamespace(
            entities=AsyncMock(return_value=[entity(AT + 5)] * proposed + [entity(AT - 5)])
        ),
        heard_questions=SimpleNamespace(count_for_session=AsyncMock(return_value=did_they_mean)),
        recaps=SimpleNamespace(enqueue=AsyncMock(return_value=True)),
        recap_worker=object(),
        import_job=None,
        recap_holds=store if holds == "default" else holds,
        recap_allowed=AsyncMock(return_value=True),
        post=AsyncMock(return_value=True),
    )
    for name in (
        "_open_name_questions", "_hold_or_queue", "_queue_recap", "_say_writing",
        "release_recap_holds", "_release_one", "recap_hold_write_now", "recheck_recap_hold",
        "recap_write_anyway", "_remind",
        "_extractor_pending", "_next_session_start",
    ):  # fmt: skip
        setattr(bot, name, getattr(DMBot, name).__get__(bot))
    return bot


class WhenTheSessionEnds(IsolatedAsyncioTestCase):
    async def test_open_questions_hold_the_recap_and_ask(self) -> None:
        bot = fake_bot(proposed=3, did_they_mean=2)
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recap_holds.hold.assert_awaited_once()
        self.assertEqual(bot.recap_holds.hold.await_args.kwargs["names_open"], 5)
        bot.recaps.enqueue.assert_not_awaited()
        channel, text = bot.post.await_args.args
        self.assertEqual((channel, text), (66, hold.question_text(5)))
        ids = [c.custom_id for c in bot.post.await_args.kwargs["view"].children]
        self.assertEqual(ids, [f"dmbot:names:review:{'c' * 32}", f"dmbot:rhold:go:{'c' * 32}:{AT}"])

    async def test_names_from_before_the_session_are_not_counted(self) -> None:
        bot = fake_bot(proposed=0)  # one proposed name, but from before the session
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recap_holds.hold.assert_not_awaited()
        bot.recaps.enqueue.assert_awaited_once()  # no questions: queued as today

    async def test_a_session_with_no_questions_queues_at_once(self) -> None:
        bot = fake_bot()
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recaps.enqueue.assert_awaited_once()
        self.assertIn("Writing a recap", bot.post.await_args.args[1])

    async def test_a_repeat_of_the_end_of_the_session_asks_nothing_more(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.hold = AsyncMock(return_value=False)
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.post.assert_not_awaited()
        bot.recaps.enqueue.assert_not_awaited()

    async def test_a_question_that_could_not_be_posted_releases_the_hold_into_a_recap(
        self,
    ) -> None:
        bot = fake_bot(proposed=2)
        bot.post = AsyncMock(side_effect=[False, True])  # the question fails, the line works
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)  # no stranded wait
        self.assertIn("Writing a recap", bot.post.await_args.args[1])

    async def test_a_failure_after_the_hold_releases_it_too(self) -> None:
        bot = fake_bot(proposed=2)
        bot.post = AsyncMock(side_effect=[RuntimeError("discord"), True])
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)

    async def test_counting_that_fails_still_queues_the_recap(self) -> None:
        bot = fake_bot(proposed=2)
        bot.memory.entities = AsyncMock(side_effect=RuntimeError("db"))
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recap_holds.hold.assert_not_awaited()
        bot.recaps.enqueue.assert_awaited_once()

    async def test_a_failure_to_hold_never_loses_the_recap(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.hold = AsyncMock(side_effect=RuntimeError("db"))
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recaps.enqueue.assert_awaited_once()

    async def test_without_a_hold_store_it_queues_as_before(self) -> None:
        bot = fake_bot(proposed=2, holds=None)
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recaps.enqueue.assert_awaited_once()


class TheCap(IsolatedAsyncioTestCase):
    """A hold nobody answers cannot wait for ever (#2174): one reminder after a day, then after
    three days the recap is written with the names as they are."""

    def aged(self, seconds: int, **kw: Any) -> Any:
        return hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - seconds, names_open=2, **kw)

    async def round_with(self, bot: Any, held: Any) -> None:
        bot.recap_holds.held = AsyncMock(return_value=[held])
        await bot.release_recap_holds(GUILD)

    async def test_no_reminder_in_the_first_day(self) -> None:
        bot = fake_bot(proposed=2)
        await self.round_with(bot, self.aged(hold.HOLD_REMIND_S - 120))
        bot.recap_holds.mark_reminded.assert_not_awaited()
        bot.post.assert_not_awaited()

    async def test_one_reminder_after_a_day_with_the_buttons_and_the_plain_words(self) -> None:
        bot = fake_bot(proposed=2)
        await self.round_with(bot, self.aged(hold.HOLD_REMIND_S + 60))
        bot.recap_holds.mark_reminded.assert_awaited_once_with(GUILD, "c" * 32, AT)
        channel, text = bot.post.await_args.args
        self.assertEqual((channel, text), (66, hold.reminder_text(2)))
        self.assertIn("about 2 days", text)
        self.assertIn("Check new names", text)
        ids = [c.custom_id for c in bot.post.await_args.kwargs["view"].children]
        self.assertEqual(ids, [f"dmbot:names:review:{'c' * 32}", f"dmbot:rhold:go:{'c' * 32}:{AT}"])
        bot.recap_holds.release.assert_not_awaited()  # still waiting

    async def test_a_reminder_already_sent_is_not_sent_again(self) -> None:
        bot = fake_bot(proposed=2)
        await self.round_with(bot, self.aged(hold.HOLD_REMIND_S + 60, reminded=True))
        bot.recap_holds.mark_reminded.assert_not_awaited()
        bot.post.assert_not_awaited()

    async def test_a_reminder_claimed_by_another_round_is_not_posted_twice(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.mark_reminded = AsyncMock(return_value=False)
        await self.round_with(bot, self.aged(hold.HOLD_REMIND_S + 60))
        bot.post.assert_not_awaited()

    async def test_a_reminder_that_could_not_be_posted_is_tried_again(self) -> None:
        bot = fake_bot(proposed=2)
        bot.post = AsyncMock(return_value=False)
        await self.round_with(bot, self.aged(hold.HOLD_REMIND_S + 60))
        bot.recap_holds.unmark_reminded.assert_awaited_once_with(GUILD, "c" * 32, AT)

    async def test_the_reminder_counts_the_time_left_from_the_holds_age(self) -> None:
        bot = fake_bot(proposed=2)
        await self.round_with(bot, self.aged(hold.HOLD_REMIND_S + 60))
        self.assertIn("about 2 days", bot.post.await_args.args[1])
        late = fake_bot(proposed=2)  # the reminder came late: little time is left
        await self.round_with(late, self.aged(hold.HOLD_MAX_S - 3600))
        self.assertIn("about a day", late.post.await_args.args[1])

    async def test_a_hold_never_asked_is_not_told_its_questions_got_no_answer(self) -> None:
        bot = fake_bot(proposed=2)  # questions open, but the DM was never asked (bot was down)
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60, asked=False))
        bot.recap_holds.release.assert_awaited_once()
        self.assertNotIn(hold.EXPIRED_NOTE, bot.post.await_args.args[1])

    async def test_after_three_days_the_recap_is_written_with_the_names_as_they_are(self) -> None:
        bot = fake_bot(proposed=2)  # two questions still open
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60))
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)
        text = bot.post.await_args.args[1]
        self.assertIn(hold.EXPIRED_NOTE, text)
        self.assertIn("Writing a recap", text)
        bot.recap_holds.mark_reminded.assert_not_awaited()

    async def test_a_reminder_that_raises_gives_the_claim_back(self) -> None:
        bot = fake_bot(proposed=2)
        bot.post = AsyncMock(side_effect=RuntimeError("discord"))
        bot.recap_holds.held = AsyncMock(return_value=[self.aged(hold.HOLD_REMIND_S + 60)])
        await bot.release_recap_holds(GUILD)  # (the round logs and goes on)
        bot.recap_holds.unmark_reminded.assert_awaited_once_with(GUILD, "c" * 32, AT)

    async def test_an_answer_a_moment_before_the_deadline_is_not_called_no_answer(self) -> None:
        bot = fake_bot()  # nothing left open when counted
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60))
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)
        text = bot.post.await_args.args[1]
        self.assertNotIn(hold.EXPIRED_NOTE, text)  # the plain "Writing a recap" line
        self.assertIn("Writing a recap", text)

    async def test_the_cap_asks_the_plan_about_this_campaign_and_says_nothing_when_gone(
        self,
    ) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.release = AsyncMock(return_value=hold.GONE)  # an answer got there first
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60))
        bot.recap_allowed.assert_awaited_once_with(GUILD, "c" * 32, None)
        bot.post.assert_not_awaited()

    async def test_ages_are_measured_with_the_stores_clock(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.now = lambda: time.time() + hold.HOLD_MAX_S + 120  # the store's clock
        await self.round_with(bot, self.aged(60))  # young by the bot's own clock
        bot.recap_holds.release.assert_awaited_once()

    async def test_just_under_three_days_still_waits(self) -> None:
        bot = fake_bot(proposed=2)
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S - 120, reminded=True))
        bot.recap_holds.release.assert_not_awaited()

    async def test_the_cap_still_needs_the_plan_and_the_dms_switch(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_allowed = AsyncMock(return_value=False)  # lacks it, or a blip: kept
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60))
        bot.recap_holds.release.assert_not_awaited()
        off = campaign()
        off.recaps_text = False
        bot.campaigns.get = AsyncMock(return_value=off)  # the DM chose no recap: dropped, none made
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60))
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT, queue=False)

    async def test_a_silent_hold_also_ends_at_the_cap(self) -> None:
        bot = fake_bot(extractor=True)
        await self.round_with(bot, self.aged(hold.HOLD_MAX_S + 60, asked=False))
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)


class WhenTheDmPressesWriteARecap(IsolatedAsyncioTestCase):
    async def test_it_waits_for_the_name_answers_like_any_session(self) -> None:
        bot = fake_bot(proposed=2)
        result = await bot.recap_write_anyway(GUILD, "c" * 32, AT, 66)
        self.assertEqual(result, "held")
        bot.recap_holds.hold.assert_awaited_once()
        bot.recaps.enqueue.assert_not_awaited()
        self.assertEqual(bot.post.await_args.args, (66, hold.question_text(2)))

    async def test_with_nothing_to_answer_it_is_written_at_once(self) -> None:
        bot = fake_bot()
        self.assertEqual(await bot.recap_write_anyway(GUILD, "c" * 32, AT, 66), "queued")
        bot.recaps.enqueue.assert_awaited_once()

    async def test_a_repeat_press_while_it_waits_says_held_and_after_a_job_says_already(
        self,
    ) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.hold = AsyncMock(return_value=False)  # held already (get finds the hold)
        self.assertEqual(await bot.recap_write_anyway(GUILD, "c" * 32, AT, 66), "held")
        bot.recap_holds.get = AsyncMock(return_value=None)  # no hold: a recap job exists
        self.assertEqual(await bot.recap_write_anyway(GUILD, "c" * 32, AT, 66), "already")

    async def test_with_the_extractor_on_and_nothing_open_the_press_writes_at_once(self) -> None:
        bot = fake_bot(extractor=True)  # the extractor already ran; no questions are open
        self.assertEqual(await bot.recap_write_anyway(GUILD, "c" * 32, AT, 66), "queued")
        bot.recap_holds.hold.assert_not_awaited()  # never a silent hold for a press
        bot.recaps.enqueue.assert_awaited_once()

    async def test_the_end_of_a_session_still_holds_silently_for_the_extractor(self) -> None:
        bot = fake_bot(extractor=True)
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        bot.recap_holds.hold.assert_awaited_once()
        self.assertFalse(bot.recap_holds.hold.await_args.kwargs["asked"])

    async def test_a_session_that_cannot_be_found_is_written_without_a_hold_and_logged(
        self,
    ) -> None:
        bot = fake_bot(proposed=2)
        bot.transcripts.sessions = AsyncMock(return_value=[])
        with self.assertLogs("dmbot.bot", level="WARNING"):
            self.assertEqual(await bot.recap_write_anyway(GUILD, "c" * 32, AT, 66), "queued")
        bot.recap_holds.hold.assert_not_awaited()


class EachRound(IsolatedAsyncioTestCase):
    def held(self) -> Any:
        return hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()))

    async def test_answered_questions_release_the_recap_and_say_so(self) -> None:
        bot = fake_bot()
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)
        self.assertIn("Writing a recap", bot.post.await_args.args[1])

    async def test_open_questions_keep_it_held(self) -> None:
        bot = fake_bot(did_they_mean=1)
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_not_awaited()
        bot.post.assert_not_awaited()
        # Each round keeps the website's count current.
        bot.recap_holds.set_names_open.assert_awaited_once_with(GUILD, "c" * 32, AT, 1)

    async def test_the_count_is_written_when_it_reaches_zero_too(self) -> None:
        bot = fake_bot()
        bot.recap_allowed = AsyncMock(return_value=False)  # the plan blocks: the hold stays
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.set_names_open.assert_awaited_once_with(GUILD, "c" * 32, AT, 0)

    async def test_recaps_the_dm_switched_off_drop_the_hold_and_write_nothing(self) -> None:
        bot = fake_bot()
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        off = campaign()
        off.recaps_text = False
        bot.campaigns.get = AsyncMock(return_value=off)
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT, queue=False)
        bot.post.assert_not_awaited()

    async def test_a_plan_that_cannot_be_checked_keeps_the_hold_for_the_next_round(self) -> None:
        bot = fake_bot()
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        bot.recap_allowed = AsyncMock(return_value=False)  # a blip, or the plan lacks it
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_not_awaited()  # nothing is destroyed
        bot.post.assert_not_awaited()

    async def test_one_campaigns_trouble_does_not_hold_up_the_next(self) -> None:
        bot = fake_bot()
        second = hold.Hold("d" * 32, AT, "s2", DM, 67, int(time.time()))
        bot.recap_holds.held = AsyncMock(return_value=[self.held(), second])
        calls = {"n": 0}
        real = bot._open_name_questions

        async def flaky(*args: Any, **kwargs: Any) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database")
            return int(await real(*args, **kwargs))

        bot._open_name_questions = flaky
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "d" * 32, AT)

    async def test_a_later_sessions_names_do_not_keep_it_waiting(self) -> None:
        bot = fake_bot()
        bot.memory.entities = AsyncMock(return_value=[entity(AT + 10), entity(AT + 500)])
        self.assertEqual(await bot._open_name_questions(GUILD, "c" * 32, "s1", AT), 2)
        bot.transcripts.next_session_start = AsyncMock(
            return_value=AT + 100
        )  # the next session began at AT + 100: counted by session, not by when it was held
        self.assertEqual(await bot._open_name_questions(GUILD, "c" * 32, "s1", AT), 1)

    async def test_a_hold_the_extractor_may_still_add_to_stays_silent(self) -> None:
        bot = fake_bot(extractor=True)
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)
        self.assertEqual(bot.recap_holds.hold.await_args.kwargs["asked"], False)
        bot.post.assert_not_awaited()  # nothing to ask yet
        bot.recaps.enqueue.assert_not_awaited()

    async def test_after_the_extractor_no_questions_writes_the_recap(self) -> None:
        bot = fake_bot(extractor=True)
        bot.recap_holds.get = AsyncMock(
            return_value=hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()), asked=False)
        )
        await bot.recheck_recap_hold(SimpleNamespace(
            guild_id=GUILD, campaign_id="c" * 32, transcript_session_id="s1"
        ))  # fmt: skip
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)
        self.assertIn("Writing a recap", bot.post.await_args.args[1])

    async def test_after_the_extractor_new_names_ask_the_dm_once(self) -> None:
        bot = fake_bot(extractor=True, proposed=2)
        bot.recap_holds.get = AsyncMock(
            return_value=hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()), asked=False)
        )
        bot.recap_holds.mark_asked = AsyncMock(return_value=True)
        t = SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, transcript_session_id="s1")
        await bot.recheck_recap_hold(t)
        bot.recap_holds.release.assert_not_awaited()
        self.assertEqual(bot.post.await_args.args, (66, hold.question_text(2)))
        bot.recap_holds.mark_asked.assert_awaited_once_with(GUILD, "c" * 32, AT)

    async def test_a_hold_already_asked_is_left_alone_by_the_recheck(self) -> None:
        bot = fake_bot(extractor=True)
        await bot.recheck_recap_hold(SimpleNamespace(
            guild_id=GUILD, campaign_id="c" * 32, transcript_session_id="s1"
        ))  # fmt: skip
        bot.recap_holds.release.assert_not_awaited()
        bot.post.assert_not_awaited()

    async def test_a_silent_hold_waits_for_the_extractor_but_not_for_ever(self) -> None:
        bot = fake_bot()
        silent = hold.Hold("c" * 32, AT, "s1", DM, 66, 1_000, asked=False)
        bot.recap_holds.held = AsyncMock(return_value=[silent])
        with patch("dmbot.bot.time.time", return_value=1_000 + 60):
            await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_not_awaited()
        with patch("dmbot.bot.time.time", return_value=1_000 + hold.QUIET_WAIT_S + 1):
            await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_awaited_once()

    async def test_a_silent_hold_past_the_wait_asks_when_names_are_open(self) -> None:
        bot = fake_bot(proposed=2)
        silent = hold.Hold("c" * 32, AT, "s1", DM, 66, 1_000, asked=False)
        bot.recap_holds.held = AsyncMock(return_value=[silent])
        bot.recap_holds.mark_asked = AsyncMock(return_value=True)
        with patch("dmbot.bot.time.time", return_value=1_000 + hold.QUIET_WAIT_S + 1):
            await bot.release_recap_holds(GUILD)
        self.assertEqual(bot.post.await_args.args, (66, hold.question_text(2)))
        bot.recap_holds.mark_asked.assert_awaited_once_with(GUILD, "c" * 32, AT)
        bot.recap_holds.release.assert_not_awaited()

    async def test_a_silent_hold_past_the_wait_whose_question_cannot_be_posted_is_released(
        self,
    ) -> None:
        bot = fake_bot(proposed=2)
        silent = hold.Hold("c" * 32, AT, "s1", DM, 66, 1_000, asked=False)
        bot.recap_holds.held = AsyncMock(return_value=[silent])
        bot.post = AsyncMock(side_effect=[False, True])  # the question fails, the line works
        with patch("dmbot.bot.time.time", return_value=1_000 + hold.QUIET_WAIT_S + 1):
            await bot.release_recap_holds(GUILD)
        bot.recap_holds.unmark_asked.assert_awaited_once_with(GUILD, "c" * 32, AT)  # claim back
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)

    async def test_the_round_and_the_recheck_never_both_ask(self) -> None:
        bot = fake_bot(proposed=2)
        silent = hold.Hold("c" * 32, AT, "s1", DM, 66, 1_000, asked=False)
        bot.recap_holds.held = AsyncMock(return_value=[silent])
        bot.recap_holds.mark_asked = AsyncMock(return_value=False)  # the other one claimed it
        with patch("dmbot.bot.time.time", return_value=1_000 + hold.QUIET_WAIT_S + 1):
            await bot.release_recap_holds(GUILD)
        bot.post.assert_not_awaited()
        bot.recap_holds.release.assert_not_awaited()

    async def test_the_recheck_has_the_same_gates_as_every_round(self) -> None:
        t = SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, transcript_session_id="s1")
        off = fake_bot(extractor=True)
        off.recap_holds.get = AsyncMock(
            return_value=hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()), asked=False)
        )
        off.campaigns.get = AsyncMock(
            return_value=SimpleNamespace(**{**vars(campaign()), "recaps_text": False})
        )
        await off.recheck_recap_hold(t)  # the DM switched recaps off meanwhile: the hold goes
        off.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT, queue=False)
        barred = fake_bot(extractor=True)
        barred.recap_holds.get = AsyncMock(
            return_value=hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()), asked=False)
        )
        barred.recap_allowed = AsyncMock(return_value=False)  # the plan lacks it, or a blip
        await barred.recheck_recap_hold(t)
        barred.recap_holds.release.assert_not_awaited()

    async def test_names_the_extractor_proposes_hold_the_recap_until_answered(self) -> None:
        """The ruling, in the real order: held silently, the extractor proposes names, the DM is
        asked once, the rounds keep it held, and only when none are left is the recap queued."""
        bot = fake_bot(extractor=True)
        made = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()), asked=False)
        proposed: list[Any] = []
        bot.memory.entities = AsyncMock(side_effect=lambda *a, **k: list(proposed))
        bot.recap_holds.get = AsyncMock(side_effect=lambda *a: made)
        bot.recap_holds.held = AsyncMock(side_effect=lambda *a: [made])
        asked = {"n": 0}

        async def mark(*_: Any) -> bool:
            nonlocal made
            asked["n"] += 1
            made = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()), asked=True)
            return True

        bot.recap_holds.mark_asked = mark
        t = SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, transcript_session_id="s1")
        await bot._hold_or_queue(campaign(), "s1", AT, 66, DM)  # session end: nothing open yet
        bot.recaps.enqueue.assert_not_awaited()
        bot.post.assert_not_awaited()
        proposed += [entity(AT + 5), entity(AT + 6)]  # ... then the extractor proposes two names
        await bot.recheck_recap_hold(t)
        self.assertEqual(bot.post.await_args.args, (66, hold.question_text(2)))
        self.assertEqual(asked["n"], 1)
        bot.post.reset_mock()
        await bot.release_recap_holds(GUILD)  # rounds keep it held and ask nothing more
        await bot.recheck_recap_hold(t)
        bot.recap_holds.release.assert_not_awaited()
        bot.post.assert_not_awaited()
        proposed.clear()  # the DM answered them all
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)

    async def test_recaps_off_everywhere_has_its_own_answer(self) -> None:
        bot = fake_bot()
        bot.settings.recaps_on = False
        bot.recaps = object()
        self.assertEqual(await bot.recap_hold_write_now(GUILD, "c" * 32, AT), "off")

    async def test_a_campaign_that_is_gone_drops_its_hold(self) -> None:
        bot = fake_bot()
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        bot.campaigns.get = AsyncMock(return_value=None)
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT, queue=False)

    async def test_a_release_that_another_got_to_first_says_nothing(self) -> None:
        bot = fake_bot()
        bot.recap_holds.held = AsyncMock(return_value=[self.held()])
        bot.recap_holds.release = AsyncMock(return_value=hold.GONE)
        await bot.release_recap_holds(GUILD)
        bot.post.assert_not_awaited()  # answering never writes (or announces) twice


class TheStepOrder(IsolatedAsyncioTestCase):
    async def test_the_recap_is_held_before_the_extractor_and_counted_again_after(self) -> None:
        # The real step list, every step a stand-in that records its name (#2173).
        bot = AsyncMock()
        table = MagicMock()
        table.transcript_channel_id = None
        await DMBot._after_session(bot, table, 0, True)
        wanted = ("suggest_names", "recap_session", "extract_claims", "recheck_recap_hold")
        called = [c[0] for c in bot.mock_calls if c[0] in wanted]
        self.assertEqual(called, list(wanted))


def press(bot: Any, user: int = DM) -> tuple[Any, Any]:
    button = screen.WriteNowButton("c" * 32, AT)
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=user),
        channel_id=66,
        message=SimpleNamespace(id=9, content="📝 3 name questions from tonight."),
        response=SimpleNamespace(
            is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock()
        ),
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return button, interaction


class TheButton(IsolatedAsyncioTestCase):
    async def test_the_line_goes_to_the_dm_screen_the_hold_stored(self) -> None:
        bot = fake_bot(proposed=3)
        button, i = press(bot)
        i.channel_id = 12345  # pressed somewhere else
        await button.callback(i)
        self.assertEqual(bot.post.await_args.args[0], 66)

    async def test_write_the_recap_now_queues_it_and_closes_the_line(self) -> None:
        bot = fake_bot(proposed=3)
        button, i = press(bot)
        await button.callback(i)
        bot.recap_holds.release.assert_awaited_once_with(GUILD, "c" * 32, AT)
        self.assertIn("Writing a recap", bot.post.await_args.args[1])
        self.assertIsNone(i.edit_original_response.await_args.kwargs["view"])
        self.assertIn("Writing the recap.", i.edit_original_response.await_args.kwargs["content"])

    async def test_only_a_dm_of_the_campaign_may_press_it(self) -> None:
        bot = fake_bot(proposed=3)
        button, i = press(bot, user=99)
        await button.callback(i)
        bot.recap_holds.release.assert_not_awaited()
        self.assertEqual(i.response.send_message.await_args.args[0], screen.NOT_YOURS)

    async def test_a_second_press_says_it_is_already_being_written(self) -> None:
        bot = fake_bot()
        bot.recap_holds.release = AsyncMock(return_value=hold.GONE)
        button, i = press(bot)
        await button.callback(i)
        bot.post.assert_not_awaited()
        self.assertIn(
            "Already being written.", i.edit_original_response.await_args.kwargs["content"]
        )

    async def test_a_plan_that_lost_recaps_refuses_and_keeps_the_hold(self) -> None:
        bot = fake_bot()
        bot.recap_allowed = AsyncMock(return_value=False)
        button, i = press(bot)
        await button.callback(i)
        bot.recap_holds.release.assert_not_awaited()
        i.edit_original_response.assert_not_awaited()

    async def test_the_id_survives_a_restart_and_fits_the_limit(self) -> None:
        button = screen.WriteNowButton("c" * 32, 10**11)
        custom_id = str(button.item.custom_id)
        self.assertLessEqual(len(custom_id), 100)
        found = re.fullmatch(screen._TEMPLATE, custom_id)
        assert found is not None
        again = await screen.WriteNowButton.from_custom_id(MagicMock(), button.item, found)
        self.assertEqual((again.campaign_id, again.session_at), ("c" * 32, 10**11))


class TheCount(IsolatedAsyncioTestCase):
    async def test_only_proposed_names_of_this_session_and_its_open_questions_count(self) -> None:
        bot = fake_bot(proposed=2, did_they_mean=3)
        self.assertEqual(await bot._open_name_questions(GUILD, "c" * 32, "s1", AT), 5)
        self.assertEqual(CONFIRMED, "confirmed")  # (confirmed names are never asked about)
        bot.heard_questions.count_for_session.assert_awaited_once_with(GUILD, "s1")

    async def test_without_memory_or_questions_there_is_nothing_to_wait_for(self) -> None:
        bot = fake_bot(proposed=2)
        bot.memory = None
        bot.heard_questions = None
        self.assertEqual(await bot._open_name_questions(GUILD, "c" * 32, "s1", AT), 0)
