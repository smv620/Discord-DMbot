"""The payment email (#1242): kept from the payment webhook, used before the Discord email.
Test addresses are example.com only."""

from __future__ import annotations

import json
import unittest

from dmbot import trial_jobs
from dmbot.web import accounts, entitlements_writer
from dmbot.web.payments import PaymentEvent
from dmbot.web.stripe import StripeConfig, StripeProvider, _email
from tests.pg import DatabaseTest

USER = 1_111_111_111
NOW = 1_800_000_000


class Reading(unittest.TestCase):
    def test_only_one_plain_address_is_kept(self) -> None:
        self.assertEqual(_email(" payer@example.com "), "payer@example.com")
        for bad in (None, 5, "", "no-at-sign", "a@b@example.com", "a b@example.com", "x" * 300):
            self.assertIsNone(_email(bad))


class Stored(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        async with self.db.user(USER) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, 'discord@example.com', 0, 0)",
                (USER,),
            )

    def event(self, event_id: str, email: str | None, at: int = NOW) -> PaymentEvent:
        return PaymentEvent(
            provider="stripe", event_id=event_id, kind="subscription_started", user_id=USER,
            occurred_at=at, plan="table", period_start=NOW, period_end=NOW + 30 * 86400,
            customer_id="cus_1", subscription_id="sub_1", email=email,
        )  # fmt: skip

    async def test_the_webhook_keeps_the_payment_email_and_it_comes_first(self) -> None:
        self.assertIsNone(await accounts.payment_email(self.db, USER))  # nothing yet
        self.assertEqual(await accounts.account_email(self.db, USER), "discord@example.com")
        await entitlements_writer.apply_event(
            self.db, self.event("e1", "payer@example.com"), now=NOW
        )
        self.assertEqual(await accounts.payment_email(self.db, USER), "payer@example.com")
        self.assertEqual(await accounts.account_email(self.db, USER), "payer@example.com")
        # A newer address replaces it; an event with none leaves it.
        await entitlements_writer.apply_event(
            self.db, self.event("e2", "new@example.com", NOW + 5), now=NOW
        )
        await entitlements_writer.apply_event(self.db, self.event("e3", None, NOW + 9), now=NOW)
        # An older event arriving late does not bring the old address back.
        await entitlements_writer.apply_event(
            self.db, self.event("e0", "stale@example.com", NOW - 5), now=NOW
        )
        self.assertEqual(await accounts.payment_email(self.db, USER), "new@example.com")

    async def test_try_it_emails_use_the_payment_email_before_the_discord_one(self) -> None:
        await entitlements_writer.start_try_it(self.db, USER, now=NOW)
        people = await trial_jobs.people(self.db, NOW)
        self.assertEqual([p.email for p in people], ["discord@example.com"])
        async with self.db.user(USER) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'payer@example.com' WHERE user_id = %s",
                (USER,),
            )
        people = await trial_jobs.people(self.db, NOW)
        self.assertEqual([p.email for p in people], ["payer@example.com"])

    async def test_a_customer_update_finds_the_account_by_its_customer_id(self) -> None:
        await entitlements_writer.apply_event(
            self.db, self.event("e1", "payer@example.com"), now=NOW
        )

        def changed(event_id: str, at: int, email: str, customer: str = "cus_1") -> PaymentEvent:
            return PaymentEvent(
                provider="stripe", event_id=event_id, kind="email_updated", user_id=0,
                occurred_at=at, customer_id=customer, email=email,
            )  # fmt: skip

        web = self.db  # (the website's role is held to the same door in test_account_move)
        self.assertEqual(
            await entitlements_writer.apply_event(
                web, changed("c1", NOW + 50, "moved@example.com"), now=NOW
            ),
            "applied",
        )
        self.assertEqual(await accounts.payment_email(self.db, USER), "moved@example.com")
        # An older event that arrives late does not replace it.
        await entitlements_writer.apply_event(
            web, changed("c0", NOW + 10, "old@example.com"), now=NOW
        )
        self.assertEqual(await accounts.payment_email(self.db, USER), "moved@example.com")
        # A customer DMbot has no plan for is ignored, and the plan was not touched.
        self.assertEqual(
            await entitlements_writer.apply_event(
                web, changed("c2", NOW + 60, "x@example.com", "cus_9"), now=NOW
            ),
            "ignored",
        )
        self.assertEqual(await accounts.customer_id(self.db, USER), "cus_1")


class Parsing(unittest.TestCase):
    def provider(self) -> StripeProvider:
        return StripeProvider(
            StripeConfig(
                secret_key="sk_test_not_real", prices={"table": "price_table"},
                extra_hours_price="p",
            ),
            b"w" * 32,
        )  # fmt: skip

    def test_customer_updated_becomes_an_address_change_found_by_customer_id(self) -> None:
        def raw(obj: dict[str, object]) -> bytes:
            return json.dumps(
                {"id": "evt_1", "type": "customer.updated", "created": 5, "livemode": False,
                 "data": {"object": obj}}
            ).encode()  # fmt: skip

        got = self.provider().parse(raw({"id": "cus_1", "email": "new@example.com"}))
        assert got is not None
        self.assertEqual(
            (got.kind, got.customer_id, got.email, got.user_id),
            ("email_updated", "cus_1", "new@example.com", 0),
        )
        self.assertIsNone(self.provider().parse(raw({"id": "cus_3"})))  # no address
        self.assertIsNone(self.provider().parse(raw({"email": "a@example.com"})))  # no id


if __name__ == "__main__":
    unittest.main()
