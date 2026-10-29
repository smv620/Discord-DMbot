"""The team's weekly support email (#1514): once a week from the hourly sweep, counts only, the
same sentence as the admin page, nothing without a mailer or team address, nothing for a week
with no questions, and never twice. Run as the website's own database role."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from dmbot.web import support
from dmbot.web import support_ai as ai
from dmbot.web.support import mail_week
from tests.test_support import MESSAGE, SUBJECT, TEAM
from tests.test_support_helper import ALERTS_FROM, DAY, SECRET, HelperBase
from tests.test_web_api import SITE

# Monday 2026-10-12, 08:00 UTC, and a Wednesday and the Sunday before it.
MONDAY_8 = int(datetime(2026, 10, 12, 8, tzinfo=UTC).timestamp())


class TheWeek(HelperBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = MONDAY_8 + 60

    def to_team(self) -> list[str]:
        return [str(m["Subject"]) for m in self.outbox.sent if str(m["To"]) == TEAM]

    async def test_it_goes_once_for_the_week_with_the_counts_and_the_pages_sentence(self) -> None:
        await self.ticket()
        self.assertEqual(await self.helper.weekly_report(), "sent")
        (mail,) = [m for m in self.outbox.sent if m["Subject"] == ai.WEEKLY_SUBJECT]
        self.assertEqual(str(mail["To"]), TEAM)
        self.assertEqual(str(mail["From"]), ALERTS_FROM)
        body = mail.get_content()
        week = await support.week(self.web, self.now)
        self.assertIn(support.week_line(week), body)
        self.assertIn(f"{SITE}/admin", body)
        self.assertNotIn(MESSAGE, body)  # counts only: never a ticket's words
        self.assertNotIn(SUBJECT, body)
        self.assertEqual(await self.helper.weekly_report(), "early")  # the next sweep
        self.now += 3 * DAY
        self.assertEqual(await self.helper.weekly_report(), "early")  # still the same week
        self.assertEqual(self.to_team().count(ai.WEEKLY_SUBJECT), 1)

    async def test_the_next_monday_sends_again(self) -> None:
        await self.ticket()
        self.assertEqual(await self.helper.weekly_report(), "sent")
        self.now += 7 * DAY
        await self.ticket()
        self.assertEqual(await self.helper.weekly_report(), "sent")
        self.assertEqual(self.to_team().count(ai.WEEKLY_SUBJECT), 2)

    async def test_a_restart_never_doubles_it(
        self,
    ) -> None:
        await self.ticket()
        self.assertEqual(await self.helper.weekly_report(), "sent")
        self.build()  # a restart: a new helper, the same database
        self.assertEqual(await self.helper.weekly_report(), "early")
        self.assertEqual(self.to_team().count(ai.WEEKLY_SUBJECT), 1)

    async def test_a_sweep_before_monday_8_has_nothing_new_to_send(self) -> None:
        await self.ticket()
        self.now = MONDAY_8 - 60  # Sunday night: last week's mail is the one that is due
        self.assertEqual(await self.helper.weekly_report(), "sent")  # (first run after deploy)
        self.now = MONDAY_8 - 30
        self.assertEqual(await self.helper.weekly_report(), "early")

    async def test_two_sweeps_at_once_send_it_once(self) -> None:
        await self.ticket()
        week = support.mail_week(self.now)
        won = await asyncio.gather(
            support.claim_week(self.web, week), support.claim_week(self.web, week)
        )
        self.assertEqual(sorted(won), [False, True])

    async def test_a_failure_after_the_claim_gives_the_week_back(self) -> None:
        await self.ticket()
        real = support.week

        async def broken(*_: Any, **__: Any) -> Any:
            raise RuntimeError("database blip")

        with patch.object(support, "week", broken), self.assertRaises(RuntimeError):
            await self.helper.weekly_report()
        self.assertEqual(await self.helper.weekly_report(), "sent")  # the next sweep tries again
        self.assertIs(support.week, real)

    async def test_only_the_admin_door_can_see_the_mark(self) -> None:
        await self.ticket()
        await self.helper.weekly_report()
        async with self.web.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM support_week_mail")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # row-level security: no door open, no rows

    async def test_a_week_with_no_questions_sends_nothing_and_stays_quiet_all_week(self) -> None:
        self.assertEqual(await self.helper.weekly_report(), "quiet")
        self.assertEqual(self.to_team().count(ai.WEEKLY_SUBJECT), 0)
        await self.ticket()  # a question on Wednesday does not turn into a mid-week email
        self.now += 2 * DAY
        self.assertEqual(await self.helper.weekly_report(), "early")

    async def test_nothing_without_a_mailer_or_a_team_address(self) -> None:
        await self.ticket()
        for mailer, team in ((None, TEAM), (self.mailer, "")):
            helper = ai.SupportHelper(
                self.web, self.fake, mailer, team_email=team, site_url=SITE,
                link_secret=SECRET, clock=lambda: float(self.now),
            )  # fmt: skip
            self.assertEqual(await helper.weekly_report(), "off")

    async def test_a_send_that_fails_is_tried_again_at_the_next_sweep(self) -> None:
        await self.ticket()
        self.outbox.fail = True
        self.assertEqual(await self.helper.weekly_report(), "failed")
        self.outbox.fail = False
        self.assertEqual(await self.helper.weekly_report(), "sent")


class TheSentenceAndTheClock(unittest.TestCase):
    def test_the_sentence_is_the_admin_pages_word_for_word(self) -> None:
        # The same cases as web/test/admin-week.test.tsx.
        reasons: dict[Any, int] = {
            "unhappy": 1,
            "asked": 1,
            "limit": 0,
            "no-email": 0,
            "helper-down": 0,
        }
        none: dict[Any, int] = {code: 0 for code in reasons}
        self.assertEqual(
            support.week_line(support.Week(12, 9, 2, 1, reasons)),
            "This week: 12 questions, 9 answered by DMbot's helper, 2 by the team, 1 waiting. "
            "Helper handed over 2 (unhappy 1, asked 1).",
        )
        self.assertEqual(
            support.week_line(support.Week(1, 0, 0, 1, none)),
            "This week: 1 question, 0 answered by DMbot's helper, 0 by the team, 1 waiting.",
        )
        self.assertEqual(
            support.week_line(support.Week(0, 0, 0, 0, none)), "No questions this week."
        )

    def test_the_week_changes_at_monday_8_utc(self) -> None:
        def at(day: int, hour: int) -> int:
            return mail_week(int(datetime(2026, 10, day, hour, tzinfo=UTC).timestamp()))

        monday = datetime(2026, 10, 12).toordinal()
        self.assertEqual(at(12, 8), monday)
        self.assertEqual(at(18, 23), monday)  # Sunday night
        self.assertEqual(at(12, 7), monday - 7)  # before Monday 8:00: still last week's
        self.assertEqual(at(19, 8), monday + 7)
