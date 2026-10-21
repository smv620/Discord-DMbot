"""A session's recap waits for the DM's name answers (#2169). Invented fixtures only."""

from __future__ import annotations

import asyncio
import dataclasses
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
from dmbot.ui.decision_lines import DecisionLines
from dmbot.ui.recap_hold import hold_line_key
from tests.pg import DatabaseTest

GUILD = 5101
OTHER = 5102
DM = 7
AT = 1000
GROUP = "a" * 32  # an import's id: 32 hex, as the button's id carries it


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

    async def test_the_group_of_a_hold_is_kept(self) -> None:
        await self.holds.hold(
            GUILD, self.campaign.id, AT, session_id="s1", dm_user_id=DM, channel_id=66,
            group_id=GROUP,
        )  # fmt: skip
        await self.hold_it(AT + 1)
        groups = {h.session_at: h.group_id for h in await self.holds.held(GUILD)}
        self.assertEqual(groups, {AT: GROUP, AT + 1: ""})
        got = await self.holds.get(GUILD, self.campaign.id, AT)
        assert got is not None
        self.assertEqual(got.group_id, GROUP)

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


def entity(created_at: int, status: str = PROPOSED, id: str = "") -> Any:
    return SimpleNamespace(created_at=created_at, status=status, id=id or f"e{created_at}")


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
        _group_made={},
        decision_lines=DecisionLines(),
        user=SimpleNamespace(id=1),
        _import_queued={},
        settings=SimpleNamespace(site_url="", recaps_on=True, story_extractor_on=extractor),
        extractor_ai=object() if extractor else None,
        transcripts=SimpleNamespace(
            next_session_start=AsyncMock(return_value=AT + 10_000),
            session=AsyncMock(return_value=SimpleNamespace(id="s1", started_at=AT)),
            sessions=AsyncMock(return_value=[SimpleNamespace(id="s1", started_at=AT)]),
        ),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        memory=SimpleNamespace(
            entities=AsyncMock(
                return_value=[entity(AT + 5, id=f"p{n}") for n in range(proposed)]
                + [entity(AT - 5)]
            ),
            deferred_names=AsyncMock(return_value=set()),  # no "Later" answers
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
        "recap_write_anyway", "_remind", "_say_group_writing", "_close_hold_lines",
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
        self.assertIn("in about 2 days", text)
        self.assertIn("Check new names", text)
        ids = [c.custom_id for c in bot.post.await_args.kwargs["view"].children]
        self.assertEqual(ids, [f"dmbot:names:review:{'c' * 32}", f"dmbot:rhold:go:{'c' * 32}:{AT}"])
        bot.recap_holds.release.assert_not_awaited()  # still waiting

    async def test_the_email_goes_after_a_day_when_emails_are_on(self) -> None:
        bot = fake_bot(proposed=2)
        bot.mailer, bot.settings.site_url = object(), "https://dmbot.example.org"
        bot._email_hold = DMBot._email_hold.__get__(bot)
        with patch("dmbot.bot.remind_the_dm", new=AsyncMock(return_value="sent")) as email:
            await self.round_with(bot, self.aged(hold.HOLD_REMIND_S + 60))
        email.assert_awaited_once()
        args = email.await_args.args
        self.assertEqual(args[3:], (GUILD, "c" * 32, DM, AT, 2, 1))  # a count and the day number

    async def test_no_email_in_the_first_day_when_off_or_without_a_mailer(self) -> None:
        for age, emails_on, mailer in (
            (hold.HOLD_REMIND_S - 120, True, object()),
            (hold.HOLD_REMIND_S + 60, False, object()),
            (hold.HOLD_REMIND_S + 60, True, None),
        ):
            bot = fake_bot(proposed=2)
            bot.mailer = mailer
            bot._email_hold = DMBot._email_hold.__get__(bot)
            bot.campaigns.get.return_value.recaps_email = emails_on
            with patch("dmbot.bot.remind_the_dm", new=AsyncMock()) as email:
                await self.round_with(bot, self.aged(age))
            email.assert_not_awaited()

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
        self.assertIn("in about 2 days", bot.post.await_args.args[1])
        late = fake_bot(proposed=2)  # the reminder came late: little time is left
        await self.round_with(late, self.aged(hold.HOLD_MAX_S - 3600))
        self.assertIn("within a day", late.post.await_args.args[1])

    async def test_the_wording_changes_at_about_twenty_hours_left(self) -> None:
        self.assertIn("within a day", hold.reminder_text(2, hold.LATE_REMINDER_S - 60))
        self.assertIn("in about a day", hold.reminder_text(2, hold.LATE_REMINDER_S + 60))
        self.assertIn("in about 2 days", hold.reminder_text(2))

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


class AnImportsHolds(IsolatedAsyncioTestCase):
    """The holds of one import go together (#2106): one question, one reminder, one expiry line,
    one "Write the recap now" for the whole group."""

    def group(self, age: int, n: int = 3, **kw: Any) -> list[Any]:
        return [
            hold.Hold(
                "c" * 32,
                AT + i,
                f"s{i}",
                DM,
                66,
                int(time.time()) - age,
                names_open=2,
                group_id=GROUP,
                **kw,
            )
            for i in range(n)
        ]

    async def test_one_reminder_for_the_whole_import(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.held = AsyncMock(return_value=self.group(hold.HOLD_REMIND_S + 60))
        await bot.release_recap_holds(GUILD)
        claimed = [c.args[2] for c in bot.recap_holds.mark_reminded.await_args_list]
        self.assertEqual(claimed, [AT, AT + 1, AT + 2])  # the oldest, then the rest of the group
        self.assertEqual(bot.post.await_count, 1)
        self.assertIn("6 name questions", bot.post.await_args.args[1])  # the group's, not one's

    async def test_a_released_lead_does_not_hand_the_group_a_second_reminder(self) -> None:
        bot = fake_bot(proposed=2)
        left = self.group(hold.HOLD_REMIND_S + 60)[1:]  # the oldest was answered and released
        # What the first reminder stored: every member of the group was claimed with it.
        bot.recap_holds.held = AsyncMock(
            return_value=[dataclasses.replace(m, reminded=True) for m in left]
        )
        await bot.release_recap_holds(GUILD)
        bot.recap_holds.mark_reminded.assert_not_awaited()
        bot.post.assert_not_awaited()

    async def test_a_live_holds_reminder_is_its_own_even_beside_an_import(self) -> None:
        bot = fake_bot(proposed=2)
        live = hold.Hold(
            "c" * 32, AT - 1, "live", DM, 66, int(time.time()) - hold.HOLD_REMIND_S - 60
        )
        bot.recap_holds.held = AsyncMock(
            return_value=[*self.group(hold.HOLD_REMIND_S + 60, 2), live]
        )
        await bot.release_recap_holds(GUILD)
        self.assertEqual(bot.post.await_count, 2)  # the import's reminder, and the live one's
        self.assertIn(AT - 1, [c.args[2] for c in bot.recap_holds.mark_reminded.await_args_list])

    async def test_one_expiry_line_and_all_of_them_released(self) -> None:
        bot = fake_bot(proposed=2)
        bot.recap_holds.held = AsyncMock(return_value=self.group(hold.HOLD_MAX_S + 60))
        await bot.release_recap_holds(GUILD)
        self.assertEqual(bot.recap_holds.release.await_count, 3)
        self.assertEqual(bot.post.await_count, 1)  # one line, however many sessions
        text = bot.post.await_args.args[1]
        self.assertIn("Writing 3 recaps from your imported sessions", text)
        self.assertIn(hold.EXPIRED_NOTE, text)

    async def test_write_it_now_writes_them_all_with_one_line(self) -> None:
        bot = fake_bot(proposed=2)
        members = self.group(100)
        bot.recap_holds.held = AsyncMock(return_value=members)
        bot.recap_holds.get = AsyncMock(return_value=members[0])
        self.assertEqual(await bot.recap_hold_write_now(GUILD, "c" * 32, AT), "queued")
        self.assertEqual(bot.recap_holds.release.await_count, 3)
        self.assertEqual(bot.post.await_count, 1)
        self.assertIn("Writing 3 recaps", bot.post.await_args.args[1])

    async def test_answers_release_them_one_by_one_and_only_the_last_says_so(self) -> None:
        bot = fake_bot()  # nothing left to answer
        members = self.group(100, 3)
        remaining = list(members)

        async def release(gid: int, cid: str, at: int, **kw: Any) -> str:
            remaining[:] = [m for m in remaining if m.session_at != at]
            return hold.QUEUED

        bot.recap_holds.release = release
        bot.recap_holds.held = AsyncMock(side_effect=lambda gid: list(remaining))
        for m in members:
            await bot._release_one(bot.recap_holds, GUILD, m, group=members)
        self.assertEqual(bot.post.await_count, 1)  # three recaps started, one line
        self.assertIn("Writing 3 recaps", bot.post.await_args.args[1])

    async def test_one_recap_made_is_never_told_as_one_recaps(self) -> None:
        bot = fake_bot(proposed=2)
        members = self.group(hold.HOLD_MAX_S + 60, 2)
        bot.recap_holds.held = AsyncMock(return_value=members)
        bot.recap_holds.release = AsyncMock(side_effect=[hold.QUEUED, hold.ALREADY])
        await bot.release_recap_holds(GUILD)
        text = bot.post.await_args.args[1]
        self.assertIn("Writing a recap of this session", text)
        self.assertNotIn("1 recaps", text)
        bot.post.reset_mock()
        bot.recap_holds.release = AsyncMock(side_effect=[hold.ALREADY, hold.QUEUED])
        bot.recap_holds.get = AsyncMock(return_value=members[0])
        bot.recap_holds.held = AsyncMock(return_value=members)
        await bot.recap_hold_write_now(GUILD, "c" * 32, AT)  # the press, too
        self.assertIn("Writing a recap of this session", bot.post.await_args.args[1])

    async def test_two_live_recaps_each_keep_their_own_line(self) -> None:
        bot = fake_bot()
        await bot._say_writing(campaign(), 66, AT)
        await bot._say_writing(campaign(), 66, AT + 5)
        self.assertEqual(bot.post.await_count, 2)  # no debounce: each is told, with its own page

    async def test_a_group_never_takes_in_another_campaigns_hold(self) -> None:
        mine = self.group(100, 2)
        other = dataclasses.replace(mine[0], campaign_id="d" * 32)
        self.assertEqual(hold.group_of([*mine, other], mine[0]), mine)

    async def test_a_quiet_hold_is_made_without_asking_again(self) -> None:
        bot = fake_bot(proposed=2)
        result = await bot._hold_or_queue(campaign(), "s9", AT, 66, DM, quiet=True, group_id=GROUP)
        self.assertEqual(result, "held")
        bot.post.assert_not_awaited()  # the import was asked about already
        kwargs = bot.recap_holds.hold.await_args.kwargs
        self.assertEqual((kwargs["group_id"], kwargs["asked"]), (GROUP, True))

    async def test_a_hold_made_for_an_import_remembers_its_group(self) -> None:
        bot = fake_bot(proposed=2)
        await bot._hold_or_queue(campaign(), "s9", AT, 66, DM, group_id=GROUP)
        self.assertEqual(bot.recap_holds.hold.await_args.kwargs["group_id"], GROUP)
        bot.post.assert_awaited_once()  # the first one asks


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


def press(bot: Any, user: int = DM, group: str = "", at: int = AT) -> tuple[Any, Any]:
    button = screen.WriteNowButton("c" * 32, at, group)
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=user),
        channel_id=66,
        message=SimpleNamespace(
            id=9,
            content="📝 3 name questions from tonight.",
            author=SimpleNamespace(id=1),  # the bot's own line
            edit=AsyncMock(),
        ),
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

    async def test_the_line_pressed_is_edited_once_not_twice(self) -> None:
        bot = fake_bot(proposed=3)
        button, i = press(bot)
        await button.callback(i)
        i.message.edit.assert_not_awaited()  # only the press's own edit touches it
        i.edit_original_response.assert_awaited_once()

    async def test_a_press_on_a_hold_that_is_gone_takes_the_other_copy_off_too(self) -> None:
        bot = fake_bot(proposed=3)
        bot.recap_holds.get = AsyncMock(return_value=None)  # no hold: written or decided before
        reminder = SimpleNamespace(id=8, content="📝 reminder", edit=AsyncMock())
        bot.decision_lines.remember(hold_line_key(GUILD, "c" * 32, AT), reminder)
        button, i = press(bot)
        await button.callback(i)
        reminder.edit.assert_awaited_once()
        self.assertIsNone(reminder.edit.await_args.kwargs["view"])

    async def test_a_press_takes_the_buttons_off_the_other_copy_too(self) -> None:
        bot = fake_bot(proposed=3)
        reminder = SimpleNamespace(id=8, content="📝 reminder", edit=AsyncMock())
        bot.decision_lines.remember(hold_line_key(GUILD, "c" * 32, AT), reminder)
        button, i = press(bot)
        await button.callback(i)
        reminder.edit.assert_awaited_once()
        self.assertIsNone(reminder.edit.await_args.kwargs["view"])

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

    async def test_a_second_press_says_it_is_being_written(self) -> None:
        bot = fake_bot()
        bot.recap_holds.release = AsyncMock(return_value=hold.GONE)
        button, i = press(bot)
        await button.callback(i)
        bot.post.assert_not_awaited()
        self.assertIn(
            hold.LINE_WRITING, i.edit_original_response.await_args.kwargs["content"]
        )  # (one wording: it is being written)

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


class TheOldLines(IsolatedAsyncioTestCase):
    """The question and the reminder of a hold lose their buttons once the hold is over (#2174)."""

    def keep_lines(self, bot: Any, *at: int, group: str = "") -> dict[int, Any]:
        """Stand-in messages the bot "posted" for each held session (an import's sessions share
        one line, under the group's key)."""
        kept = {}
        for n in at:
            message = SimpleNamespace(id=n, content=f"Questions {n}", edit=AsyncMock())
            kept[n] = message
            bot.decision_lines.remember(hold_line_key(GUILD, "c" * 32, n, group), message)
        return kept

    def track(self, bot: Any, members: list[Any]) -> None:
        """The store forgets a hold once it is released, as the real one does."""
        remaining = list(members)

        async def release(gid: int, cid: str, at: int, **kw: Any) -> str:
            remaining[:] = [m for m in remaining if m.session_at != at]
            return hold.QUEUED

        bot.recap_holds.release = release
        bot.recap_holds.held = AsyncMock(side_effect=lambda gid: list(remaining))

    def edited(self, message: Any) -> str:
        message.edit.assert_awaited_once()
        self.assertIsNone(message.edit.await_args.kwargs["view"])  # the buttons come off
        return str(message.edit.await_args.kwargs["content"])

    async def test_an_answered_hold_closes_its_line(self) -> None:
        bot = fake_bot()  # nothing left to answer
        (line,) = self.keep_lines(bot, AT).values()
        held = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - 100)
        bot.recap_holds.held = AsyncMock(return_value=[held])
        await bot.release_recap_holds(GUILD)
        self.assertIn(hold.LINE_ANSWERED, self.edited(line))

    async def test_a_hold_that_ran_out_closes_its_line_and_says_so(self) -> None:
        bot = fake_bot(proposed=2)
        (line,) = self.keep_lines(bot, AT).values()
        held = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - hold.HOLD_MAX_S - 60)
        bot.recap_holds.held = AsyncMock(return_value=[held])
        await bot.release_recap_holds(GUILD)
        self.assertIn(hold.LINE_EXPIRED, self.edited(line))

    async def test_write_it_now_closes_the_one_line_of_an_import(self) -> None:
        bot = fake_bot(proposed=2)
        (line,) = self.keep_lines(bot, AT, group=GROUP).values()
        members = [
            hold.Hold("c" * 32, AT + n, f"s{n}", DM, 66, int(time.time()) - 100, group_id=GROUP)
            for n in (0, 1)
        ]
        self.track(bot, members)
        bot.recap_holds.get = AsyncMock(return_value=members[0])
        await bot.recap_hold_write_now(GUILD, "c" * 32, AT)
        self.assertIn(hold.LINE_WRITING, self.edited(line))

    async def test_a_lone_hold_still_waiting_keeps_its_buttons(self) -> None:
        bot = fake_bot(proposed=2)
        (line,) = self.keep_lines(bot, AT).values()
        held = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - 100)
        bot.recap_holds.held = AsyncMock(return_value=[held])
        await bot.release_recap_holds(GUILD)
        line.edit.assert_not_awaited()

    async def test_write_it_now_on_a_shared_line_never_reaches_another_campaigns_hold(self) -> None:
        bot = fake_bot(proposed=2)
        other = hold.Hold("c" * 32, AT + 1, "s1", DM, 66, int(time.time()) - 100, group_id=GROUP)
        foreign = hold.Hold("d" * 32, AT + 2, "s2", DM, 66, int(time.time()) - 100, group_id=GROUP)
        released: list[tuple[str, int]] = []

        async def release(gid: int, cid: str, at: int, **kw: Any) -> str:
            released.append((cid, at))
            return hold.QUEUED

        bot.recap_holds.release = release
        bot.recap_holds.held = AsyncMock(return_value=[other, foreign])
        bot.recap_holds.get = AsyncMock(return_value=None)  # the poster was answered
        button, i = press(bot, group=GROUP)
        await button.callback(i)
        self.assertEqual(released, [("c" * 32, AT + 1)])  # not the other campaign's

    async def test_an_imports_line_stays_while_any_of_its_sessions_still_waits(self) -> None:
        bot = fake_bot()  # nothing left to answer, so each is released by its own answers
        (line,) = self.keep_lines(bot, AT, group=GROUP).values()
        members = [
            hold.Hold("c" * 32, AT + n, f"s{n}", DM, 66, int(time.time()) - 100, group_id=GROUP)
            for n in (0, 1)
        ]
        self.track(bot, members)
        await bot._release_one(bot.recap_holds, GUILD, members[0], group=list(members))
        line.edit.assert_not_awaited()  # the other session's questions are still open
        await bot._release_one(bot.recap_holds, GUILD, members[1], group=[members[1]])
        self.assertIn(hold.LINE_ANSWERED, self.edited(line))  # now it is over, once

    async def test_the_reminder_of_another_member_is_the_same_line_of_the_import(self) -> None:
        bot = fake_bot()
        first = SimpleNamespace(id=1, content="question", edit=AsyncMock())
        reminder = SimpleNamespace(id=2, content="reminder", edit=AsyncMock())
        key = hold_line_key(GUILD, "c" * 32, AT, GROUP)
        bot.decision_lines.remember(key, first)
        bot.decision_lines.remember(
            hold_line_key(GUILD, "c" * 32, AT + 1, GROUP), reminder
        )  # posted under whichever member was the oldest then: still the group's key
        members = [
            hold.Hold("c" * 32, AT + n, f"s{n}", DM, 66, int(time.time()) - 100, group_id=GROUP)
            for n in (0, 1)
        ]
        self.track(bot, members)
        await bot._release_one(bot.recap_holds, GUILD, members[0], group=list(members))
        await bot._release_one(bot.recap_holds, GUILD, members[1], group=[members[1]])
        for line in (first, reminder):
            self.edited(line)

    async def test_lines_are_kept_per_server(self) -> None:
        self.assertNotEqual(
            hold_line_key(1, "c" * 32, AT), hold_line_key(2, "c" * 32, AT)
        )  # the same campaign id on another server is another item

    async def test_a_campaign_with_recaps_off_closes_its_line(self) -> None:
        bot = fake_bot(proposed=2)
        (line,) = self.keep_lines(bot, AT).values()
        bot.campaigns.get = AsyncMock(return_value=None)  # the campaign is gone
        held = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - 100)
        bot.recap_holds.held = AsyncMock(return_value=[held])
        await bot.release_recap_holds(GUILD)
        self.assertIn(hold.LINE_NO_RECAP, self.edited(line))

    async def test_a_hold_whose_recap_was_made_another_way_still_closes_its_line(self) -> None:
        bot = fake_bot()
        (line,) = self.keep_lines(bot, AT).values()
        held = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - 100)
        bot.recap_holds.held = AsyncMock(return_value=[held])
        bot.recap_holds.release = AsyncMock(return_value=hold.ALREADY)  # a job existed
        await bot.release_recap_holds(GUILD)
        self.assertIn(hold.LINE_ANSWERED, self.edited(line))
        bot.post.assert_not_awaited()  # nothing is said about a recap that was made before

    async def test_an_import_that_ran_out_closes_its_line_once(self) -> None:
        bot = fake_bot(proposed=2)
        (line,) = self.keep_lines(bot, AT, group=GROUP).values()
        members = [
            hold.Hold(
                "c" * 32,
                AT + n,
                f"s{n}",
                DM,
                66,
                int(time.time()) - hold.HOLD_MAX_S - 60,
                group_id=GROUP,
            )
            for n in range(3)
        ]
        self.track(bot, members)
        await bot.release_recap_holds(GUILD)
        self.assertIn(hold.LINE_EXPIRED, self.edited(line))

    async def test_write_it_now_on_the_shared_line_reaches_the_import_after_its_poster_is_answered(
        self,
    ) -> None:
        bot = fake_bot(proposed=2)
        poster, other = (
            hold.Hold("c" * 32, AT + n, f"s{n}", DM, 66, int(time.time()) - 100, group_id=GROUP)
            for n in (0, 1)
        )
        remaining = [poster, other]
        released: list[int] = []

        async def release(gid: int, cid: str, at: int, **kw: Any) -> str:
            released.append(at)
            remaining[:] = [m for m in remaining if m.session_at != at]
            return hold.QUEUED

        bot.recap_holds.release = release
        bot.recap_holds.held = AsyncMock(side_effect=lambda gid: list(remaining))
        bot.recap_holds.get = AsyncMock(
            side_effect=lambda g, c, at: next((m for m in remaining if m.session_at == at), None)
        )
        await bot.recap_holds.release(GUILD, "c" * 32, poster.session_at)  # answered earlier
        released.clear()
        button, i = press(bot, group=GROUP)  # the button the shared line was posted with
        await button.callback(i)
        self.assertEqual(released, [other.session_at])  # the rest of the import was written
        self.assertEqual(remaining, [])
        self.assertIn(hold.LINE_WRITING, i.edit_original_response.await_args.kwargs["content"])
        self.assertIn("Writing", bot.post.await_args.args[1])  # and the DM is told

    async def test_a_press_on_a_line_of_an_import_that_is_over_says_it_is_being_written(
        self,
    ) -> None:
        bot = fake_bot()
        bot.recap_holds.get = AsyncMock(return_value=None)
        bot.recap_holds.held = AsyncMock(return_value=[])  # nothing of the import is held
        button, i = press(bot, group=GROUP)
        await button.callback(i)
        bot.post.assert_not_awaited()
        self.assertIn(hold.LINE_WRITING, i.edit_original_response.await_args.kwargs["content"])

    async def test_a_button_names_its_group_and_a_plain_one_does_not(self) -> None:
        plain_id = screen.WriteNowButton("c" * 32, AT).item.custom_id
        shared_id = screen.WriteNowButton("c" * 32, AT, GROUP).item.custom_id
        assert plain_id is not None and shared_id is not None
        self.assertEqual(plain_id, f"dmbot:rhold:go:{'c' * 32}:{AT}")
        self.assertEqual(shared_id, f"dmbot:rhold:go:{'c' * 32}:{AT}:{GROUP}")
        self.assertLess(len(shared_id), 100)  # Discord's limit
        for custom_id, group in ((plain_id, ""), (shared_id, GROUP)):
            match = re.fullmatch(screen._TEMPLATE, custom_id)
            assert match is not None
            back = await screen.WriteNowButton.from_custom_id(MagicMock(), MagicMock(), match)
            self.assertEqual(
                (back.campaign_id, back.session_at, back.group_id), ("c" * 32, AT, group)
            )

    async def test_an_imports_question_and_reminder_are_posted_under_the_groups_key(self) -> None:
        bot = fake_bot(proposed=2)
        posted: list[tuple[Any, Any]] = []

        async def post(channel: int, text: str, view: Any = None, **kw: Any) -> bool:
            posted.append((kw.get("remember"), view.children[1].custom_id))
            return True

        bot.post = post
        group = hold.Hold(
            "c" * 32, AT, "s1", DM, 66, int(time.time()) - hold.HOLD_REMIND_S - 60, group_id=GROUP
        )
        key = hold_line_key(GUILD, "c" * 32, AT, GROUP)
        await bot._hold_or_queue(campaign(), "s2", AT, 66, DM, group_id=GROUP)  # at the end
        bot.recap_holds.held = AsyncMock(return_value=[dataclasses.replace(group, asked=False)])
        await bot.release_recap_holds(GUILD)  # the round's question
        bot.recap_holds.held = AsyncMock(return_value=[group])
        await bot.release_recap_holds(GUILD)  # the reminder
        self.assertEqual([k for k, _ in posted], [key, key, key])
        self.assertTrue(all(c.endswith(f":{GROUP}") for _, c in posted))

    async def test_the_questions_and_the_reminder_are_posted_to_be_closed_later(self) -> None:
        bot = fake_bot(proposed=2)
        posted: list[Any] = []

        async def post(channel: int, text: str, view: Any = None, **kw: Any) -> bool:
            posted.append(kw.get("remember"))
            return True

        bot.post = post
        old = hold.Hold("c" * 32, AT, "s1", DM, 66, int(time.time()) - hold.HOLD_REMIND_S - 60)
        bot.recap_holds.held = AsyncMock(return_value=[dataclasses.replace(old, asked=False)])
        await bot.release_recap_holds(GUILD)  # the question
        bot.recap_holds.held = AsyncMock(return_value=[old])
        await bot.release_recap_holds(GUILD)  # the reminder
        await bot._hold_or_queue(campaign(), "s2", AT + 5, 66, DM)  # and at the end of a session
        key = hold_line_key(GUILD, "c" * 32, AT)
        self.assertEqual(posted[:2], [key, key])
        self.assertEqual(posted[2], hold_line_key(GUILD, "c" * 32, AT + 5))
