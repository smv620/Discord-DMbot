"""The last day of Try It and the automatic extension (#1229): the rules (pure) and the jobs
end to end with a fake clock, a fake mail transport and a fake private message. Needs
Postgres for the jobs, like the other store tests."""

from __future__ import annotations

import unittest
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit

from dmbot import account_id, link_tokens, trial_jobs, usage
from dmbot.campaigns.store import CampaignStore
from dmbot.mail import Mailer, load_mail_settings
from dmbot.trial_jobs import DAY_S, TrialJobs, TrialPerson
from dmbot.web import entitlements_writer
from tests.pg import DatabaseTest

SECRET = b"t" * 40
SITE = "https://site.example.com"
USER = 4321
OTHER = 8765
ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_USER": "sender@example.com",
    "SMTP_APP_PASSWORD": "not-a-real-password",
    "MAIL_FROM": "DMbot <sender@example.com>",
    "MAIL_POSTAL_ADDRESS": "PO Box 1, Example City",
}
START = 1_800_000_000
END = START + 30 * DAY_S


def person(**kw: object) -> TrialPerson:
    values: dict[str, object] = dict(
        user_id=1, dmbot_id="A", email="p@example.com", period_end=END, minutes=0, paid=False,
        extensions=0,
    )  # fmt: skip
    return TrialPerson(**{**values, **kw})  # type: ignore[arg-type]


class Rules(unittest.TestCase):
    def test_the_last_day_is_the_day_before_it_ends(self) -> None:
        self.assertTrue(trial_jobs.on_last_day(person(), END - 3600))
        self.assertTrue(trial_jobs.on_last_day(person(), END - DAY_S))
        self.assertFalse(trial_jobs.on_last_day(person(), END - DAY_S - 1))
        self.assertFalse(trial_jobs.on_last_day(person(), END))  # over
        self.assertFalse(trial_jobs.on_last_day(person(paid=True), END - 60))

    def test_an_extension_needs_three_days_after_the_end_no_payment_and_under_4_hours(self) -> None:
        after = END + 3 * DAY_S
        self.assertTrue(trial_jobs.extension_due(person(minutes=239), after))
        self.assertFalse(trial_jobs.extension_due(person(minutes=239), after - 1))
        self.assertFalse(trial_jobs.extension_due(person(minutes=240), after))
        self.assertFalse(trial_jobs.extension_due(person(paid=True), after))

    def test_the_hours_are_what_is_left_of_the_8_in_all(self) -> None:
        self.assertEqual(trial_jobs.extension_hours(0), 8)
        self.assertEqual(trial_jobs.extension_hours(90), 6)  # 1.5 hours used: 6 whole hours left
        self.assertEqual(trial_jobs.extension_hours(239), 4)
        self.assertEqual(trial_jobs.extension_hours(500), 0)

    def test_the_owners_words_and_personal_links(self) -> None:
        text = trial_jobs.last_day_text(
            "Ana", "https://x.example/pricing", "https://x.example/hello"
        )
        self.assertTrue(text.startswith("Hi Ana! Thanks for trying DMbot."))
        self.assertIn("we do have to pay other companies for the AI models", text)
        self.assertIn("plans at https://x.example/pricing.", text)
        link = trial_jobs._page(SITE, "/pricing", "ABC", 1000, SECRET)
        query = parse_qs(urlsplit(link).query)
        self.assertEqual(query["uid"], ["ABC"])
        self.assertTrue(link_tokens.check_link_token("ABC", query["t"][0], 1000, SECRET))
        self.assertEqual(trial_jobs._page(SITE, "/hello", "ABC", 1, b""), f"{SITE}/hello")
        self.assertEqual(trial_jobs._page("", "/hello", "ABC", 1, SECRET), "/hello")


class FakeMail:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def __call__(self, message: EmailMessage) -> None:
        self.sent.append(message)


class Jobs(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = START
        self.mail = FakeMail()
        self.dms: list[tuple[int, str]] = []
        self.dm_result = "sent"
        for uid in (USER, OTHER):
            async with self.db.user(uid) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, %s, 0, 0)",
                    (uid, f"u{uid}@example.com"),
                )
            await entitlements_writer.start_try_it(self.db, uid, now=START)

    def jobs(self, enforce: bool = True) -> TrialJobs:
        async def send_dm(user_id: int, text: str) -> trial_jobs.DmResult:
            if self.dm_result == "raise":
                raise RuntimeError("discord is down")
            if self.dm_result == "sent":
                self.dms.append((user_id, text))
            return self.dm_result  # type: ignore[return-value]

        async def name_of(user_id: int) -> str:
            return f"Name{user_id}"

        mailer = Mailer(
            self.db, load_mail_settings(ENV), transport=self.mail, site_url=SITE,
            link_secret=SECRET, clock=lambda: float(self.now),
        )  # fmt: skip
        return TrialJobs(
            self.db, mailer, enforce_plans=enforce, site_url=SITE, link_secret=SECRET,
            send_dm=send_dm, name_of=name_of, clock=lambda: float(self.now),
        )  # fmt: skip

    async def plan(self, uid: int) -> tuple[int, int]:
        async with self.db.user(uid) as conn:
            cur = await conn.execute(
                "SELECT period_end, hours_cap FROM entitlements WHERE user_id = %s", (uid,)
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["period_end"]), int(row["hours_cap"])

    async def use(self, uid: int, minutes: int) -> None:
        campaigns = CampaignStore(self.db)
        c = await campaigns.create(900 + uid, "Frostmaiden", uid)
        await usage.add_minutes(
            self.db, guild_id=900 + uid, campaign_id=c.id, owner_user_id=uid,
            session_started_at=START + 100, minutes=minutes, now=START + 200,
        )  # fmt: skip

    async def test_nothing_happens_while_plans_are_not_enforced(self) -> None:
        self.now = END - 3600
        summary = await self.jobs(enforce=False).run()
        self.assertEqual(summary, trial_jobs.Summary())
        self.assertEqual((self.dms, self.mail.sent), ([], []))

    async def test_the_free_list_is_left_alone(self) -> None:
        self.now = END - 3600
        jobs = self.jobs()
        jobs._skip = frozenset({USER})
        await jobs.run()
        self.assertEqual([u for u, _ in self.dms], [OTHER])

    async def test_private_message_records_do_not_use_up_the_email_cap(self) -> None:
        self.now = END - 3600
        await self.jobs().run()
        mailer = self.jobs()._mail
        self.assertEqual(await mailer.sent_today(), 2)  # two emails, not four records

    async def test_the_door_checks_the_rules_itself(self) -> None:
        async def extend(uid: int, now: int, days: int = 30, hours: int = 8) -> bool:
            async with self.db.unscoped() as conn:
                cur = await conn.execute(
                    "SELECT dmbot_extend_trial(%s, %s, %s, %s) AS ok", (uid, now, days, hours)
                )
                row = await cur.fetchone()
            return bool(row and row["ok"])

        late = END + 3 * DAY_S
        self.assertFalse(await extend(USER, late - 1))  # too early
        self.assertFalse(await extend(USER, late, days=90))  # not 30 days
        self.assertFalse(await extend(USER, late, hours=99))  # more than 8 hours
        await self.use(OTHER, 300)
        self.assertFalse(await extend(OTHER, late))  # over 4 hours in all
        self.assertTrue(await extend(USER, late))
        self.assertFalse(await extend(USER, late))  # a second call changes nothing
        async with self.db.unscoped() as conn:  # the door puts its setting back
            cur = await conn.execute("SELECT current_setting('dmbot.owner_sync', true) AS s")
            row = await cur.fetchone()
        self.assertIn((row or {}).get("s"), (None, ""))

    async def test_the_last_day_message_goes_once_by_message_and_by_email(self) -> None:
        self.now = END - 3600
        first = await self.jobs().run()
        again = await self.jobs().run()
        self.assertEqual((first.last_day_dms, first.last_day_emails), (2, 2))
        self.assertEqual((again.last_day_dms, again.last_day_emails), (0, 0))
        self.assertEqual(len(self.dms), 2)
        self.assertEqual(len(self.mail.sent), 2)
        text = next(t for u, t in self.dms if u == USER)
        self.assertTrue(text.startswith(f"Hi Name{USER}!"))
        ident = account_id.to_dmbot_id(USER)
        self.assertIn(f"uid={ident}", text)
        self.assertIn("/unsubscribe?uid=", self.mail.sent[0].get_content())

    async def test_a_message_that_could_not_be_delivered_is_tried_again(self) -> None:
        self.now = END - 3600
        self.dm_result = "retry"
        self.assertEqual((await self.jobs().run()).last_day_dms, 0)
        self.dm_result = "raise"  # even a failure nobody expected leaves it to try again
        self.assertEqual((await self.jobs().run()).last_day_dms, 0)
        self.dm_result = "sent"
        self.assertEqual((await self.jobs().run()).last_day_dms, 2)

    async def test_a_person_discord_will_not_message_is_tried_once(self) -> None:
        self.now = END - 3600
        self.dm_result = "closed"
        self.assertEqual((await self.jobs().run()).last_day_dms, 0)
        self.dm_result = "sent"
        self.assertEqual((await self.jobs().run()).last_day_dms, 0)  # not asked again
        self.assertEqual(len(self.mail.sent), 2)  # the email went the first time

    async def test_an_extension_adds_30_days_and_the_hours_left(self) -> None:
        await self.use(USER, 90)
        self.now = END + 3 * DAY_S
        summary = await self.jobs().run()
        self.assertEqual((summary.extended, summary.extension_emails), (2, 2))
        self.assertEqual(await self.plan(USER), (self.now + 30 * DAY_S, 6))  # 8 in all, 1.5 used
        self.assertEqual(await self.plan(OTHER), (self.now + 30 * DAY_S, 8))
        again = await self.jobs().run()
        self.assertEqual(again.extended, 0)
        # Told once, privately, at the next start.
        notice = await trial_jobs.take_extension_notice(self.db, USER, self.now)
        self.assertIn("30 more days", notice or "")
        self.assertIsNone(await trial_jobs.take_extension_notice(self.db, USER, self.now))

    async def test_it_repeats_while_under_4_hours_and_stops_after(self) -> None:
        await self.use(USER, 30)
        self.now = END + 3 * DAY_S
        await self.jobs().run()
        second_end = self.now + 30 * DAY_S
        self.now = second_end + 3 * DAY_S
        summary = await self.jobs().run()
        self.assertEqual(summary.extended, 2)  # a second extension, with a second email each
        self.assertEqual(len(self.mail.sent), 4)
        await self.use(OTHER, 250)  # now over 4 hours in all
        self.now = self.now + 33 * DAY_S
        self.assertEqual((await self.jobs().run()).extended, 1)  # only USER, who is still under

    async def test_someone_over_4_hours_or_who_paid_is_not_extended(self) -> None:
        await self.use(USER, 300)
        async with self.db.plan_writer(OTHER) as conn:
            await conn.execute(
                "INSERT INTO payment_events (provider, event_id, user_id, kind, received_at)"
                " VALUES ('fake', 'p1', %s, 'subscription_started', %s)",
                (OTHER, START),
            )
        self.now = END + 3 * DAY_S
        self.assertEqual((await self.jobs().run()).extended, 0)
        self.assertEqual(await self.plan(USER), (END, 8))

    async def test_nobody_is_extended_early(self) -> None:
        self.now = END + 3 * DAY_S - 1
        self.assertEqual((await self.jobs().run()).extended, 0)


if __name__ == "__main__":
    unittest.main()
