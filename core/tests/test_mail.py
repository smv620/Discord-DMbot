"""Sending email (#1229): settings, the once-only log, the daily cap, the offer rules
(postal address, unsubscribe) and that nothing personal is logged. A fake transport and
example.com addresses only. The sending tests need Postgres, like the other store tests."""

from __future__ import annotations

import logging
import unittest
from email.message import EmailMessage

from dmbot import account_id, link_tokens, mail
from dmbot.mail import Mailer, MailSettings, load_mail_settings
from tests.pg import DatabaseTest

ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "587",
    "SMTP_USER": "sender@example.com",
    "SMTP_APP_PASSWORD": "not-a-real-password",
    "MAIL_FROM": "DMbot <sender@example.com>",
    "MAIL_POSTAL_ADDRESS": "PO Box 1 | Example City, EX 00000",
}
SECRET = b"s" * 40
SITE = "https://site.example.com"
NOW = 1_700_000_000
USER = 1_234_567_890_123_456_789
ID = account_id.to_dmbot_id(USER)


class Settings(unittest.TestCase):
    def test_it_reads_the_environment_and_hides_the_password(self) -> None:
        s = load_mail_settings(ENV)
        self.assertTrue(s.configured)
        self.assertEqual((s.port, s.daily_cap), (587, 400))
        self.assertEqual(s.postal_address, "PO Box 1, Example City, EX 00000")
        self.assertNotIn("not-a-real", repr(s))

    def test_nothing_set_means_nothing_sent(self) -> None:
        self.assertFalse(load_mail_settings({}).configured)

    def test_bad_numbers_are_refused(self) -> None:
        for bad in ({"SMTP_PORT": "x"}, {"SMTP_PORT": "0"}, {"MAIL_DAILY_CAP": "-1"}):
            with self.assertRaises(mail.MailConfigError):
                load_mail_settings({**ENV, **bad})


class Unsubscribe(unittest.TestCase):
    def test_the_token_is_for_one_id_and_never_expires(self) -> None:
        token = link_tokens.make_unsubscribe_token(ID, SECRET)
        self.assertTrue(link_tokens.check_unsubscribe_token(ID, token, SECRET))
        other = account_id.to_dmbot_id(42)
        self.assertFalse(link_tokens.check_unsubscribe_token(other, token, SECRET))
        self.assertFalse(link_tokens.check_unsubscribe_token(ID, token, b"x" * 40))
        self.assertFalse(link_tokens.check_unsubscribe_token(ID, token[:-1] + "0", SECRET))
        self.assertFalse(link_tokens.check_unsubscribe_token("bad id", token, SECRET))

    def test_it_is_not_a_personal_page_token(self) -> None:
        page = link_tokens.make_link_token(ID, NOW, SECRET)
        self.assertFalse(link_tokens.check_unsubscribe_token(ID, page, SECRET))
        unsub = link_tokens.make_unsubscribe_token(ID, SECRET)
        self.assertFalse(link_tokens.check_link_token(ID, unsub, NOW, SECRET))


class Fake:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = False

    async def __call__(self, message: EmailMessage) -> None:
        if self.fail:
            raise OSError("smtp down for sender@example.com")
        self.sent.append(message)


class Sending(DatabaseTest):
    def mailer(self, **settings: object) -> tuple[Mailer, Fake]:
        fake = Fake()
        env = {**ENV, **{k: str(v) for k, v in settings.items()}}
        made = Mailer(
            self.db,
            load_mail_settings(env),
            transport=fake,
            site_url=SITE,
            link_secret=SECRET,
            clock=lambda: NOW,
        )
        return made, fake

    async def send(
        self,
        made: Mailer,
        kind: str = "x",
        *,
        offer: bool = False,
        to: str | None = "p@example.com",
    ) -> str:
        return await made.send(
            user_id=USER, dmbot_id=ID, kind=kind, to=to, subject="Hi", body="Hello", offer=offer
        )

    async def test_an_account_email_goes_once_per_kind(self) -> None:
        made, fake = self.mailer()
        self.assertEqual(await self.send(made, "sign-in-1"), "sent")
        self.assertEqual(await self.send(made, "sign-in-1"), "already_sent")
        self.assertEqual(await self.send(made, "sign-in-2"), "sent")
        self.assertEqual(len(fake.sent), 2)
        self.assertNotIn("Unsubscribe", fake.sent[0].get_content())

    async def test_an_offer_carries_the_unsubscribe_link_and_the_address(self) -> None:
        made, fake = self.mailer()
        self.assertEqual(await self.send(made, "trial-end", offer=True), "sent")
        text = fake.sent[0].get_content()
        token = link_tokens.make_unsubscribe_token(ID, SECRET)
        self.assertIn(f"{SITE}/unsubscribe?uid={ID}&t={token}", text)
        self.assertIn("PO Box 1, Example City, EX 00000", text)
        self.assertIn(token, str(fake.sent[0]["List-Unsubscribe"]))

    async def test_an_offer_waits_for_the_postal_address(self) -> None:
        made, fake = self.mailer(MAIL_POSTAL_ADDRESS="")
        self.assertEqual(await self.send(made, "trial-end", offer=True), "not_ready")
        self.assertEqual(fake.sent, [])
        self.assertEqual(await self.send(made, "sign-in-1"), "sent")  # account email still goes
        # And it was not marked as sent: it goes once the address is set.
        ready, fake2 = self.mailer()
        self.assertEqual(await self.send(ready, "trial-end", offer=True), "sent")
        self.assertEqual(len(fake2.sent), 1)

    async def test_someone_who_unsubscribed_gets_no_offer_but_still_gets_account_email(
        self,
    ) -> None:
        made, fake = self.mailer()
        self.assertTrue(await mail.unsubscribe(self.db, USER, now=NOW))
        self.assertFalse(await mail.unsubscribe(self.db, USER, now=NOW))
        self.assertEqual(await self.send(made, "trial-end", offer=True), "unsubscribed")
        self.assertEqual(await self.send(made, "sign-in-1"), "sent")
        self.assertEqual(len(fake.sent), 1)

    async def test_the_daily_cap_stops_the_flood_and_a_new_day_starts_again(self) -> None:
        made, fake = self.mailer(MAIL_DAILY_CAP=2)
        results = [await self.send(made, f"k{i}") for i in range(3)]
        self.assertEqual(results, ["sent", "sent", "capped"])
        self.assertEqual(await made.sent_today(), 2)
        tomorrow = Mailer(
            self.db, load_mail_settings({**ENV, "MAIL_DAILY_CAP": "2"}), transport=fake,
            site_url=SITE, link_secret=SECRET, clock=lambda: NOW + 86400,
        )  # fmt: skip
        self.assertEqual(await self.send(tomorrow, "k2"), "sent")

    async def test_a_failed_send_is_not_marked_sent_and_logs_no_address(self) -> None:
        made, fake = self.mailer()
        fake.fail = True
        with self.assertLogs("dmbot.mail", level=logging.ERROR) as logged:
            self.assertEqual(await self.send(made, "sign-in-1"), "failed")
        self.assertNotIn("example.com", "\n".join(logged.output))
        fake.fail = False
        self.assertEqual(await self.send(made, "sign-in-1"), "sent")

    async def test_no_address_or_a_bad_one_sends_nothing(self) -> None:
        made, fake = self.mailer()
        for to in (None, "", "nobody", "a@example.com\nBcc: b@example.com"):
            self.assertEqual(await self.send(made, "k", to=to), "no_address")
        self.assertEqual(fake.sent, [])

    async def test_an_unset_mailbox_sends_nothing(self) -> None:
        made = Mailer(self.db, MailSettings(), transport=Fake(), clock=lambda: NOW)
        self.assertEqual(await self.send(made), "not_ready")


if __name__ == "__main__":
    unittest.main()
