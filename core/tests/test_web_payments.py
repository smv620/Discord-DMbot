"""Payments in the web API (#435): the signed webhook is the only thing that changes a
paid plan, each event once and never backwards; Try It once; prices only from plans.json."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot import entitlements
from dmbot.config import ConfigError
from dmbot.web import accounts, entitlements_writer, grants
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider, PaymentEvent
from dmbot.web.settings import load_web_settings
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, API, SITE, FakeDiscord, settings

DAY = 86400
HOOK_SECRET = b"h" * 32


class Payments(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.provider = FakeProvider(HOOK_SECRET, SITE)
        self.app = create_app(
            settings(), self.db, FakeDiscord(), payments=self.provider, clock=lambda: self.now
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.events = 0
        await self.sign_in()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def sign_in(self) -> None:
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def post(self, path: str, body: Any = None) -> httpx.Response:
        return await self.client.post(
            path, headers={"X-DMbot-Request": "1"}, json=body if body is not None else None
        )

    async def send(self, signed: bool = True, **event: Any) -> httpx.Response:
        self.events += 1
        payload = {"id": f"evt-{self.events}", "user_id": ALICE.id, "occurred_at": self.now}
        payload.update(event)
        body = json.dumps(payload).encode()
        signature = self.provider.sign(body) if signed else "0" * 64
        return await self.client.post(
            "/webhooks/fake",
            content=body,
            headers={"x-fake-signature": signature, "content-type": "application/json"},
        )

    async def start_table(self, **changes: Any) -> httpx.Response:
        event: dict[str, Any] = {
            "kind": "subscription_started",
            "plan": "table",
            "period_start": self.now,
            "period_end": self.now + 30 * DAY,
            "customer_id": "cus_1",
            "subscription_id": "sub_1",
        }
        event.update(changes)
        return await self.send(**event)

    async def plan(self) -> entitlements.Entitlement:
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        return got

    # The webhook

    async def test_a_signed_event_starts_a_plan_with_the_caps_from_plans_json(self) -> None:
        self.assertEqual((await self.start_table(hours_cap=999)).status_code, 200)
        got = await self.plan()
        self.assertEqual(
            (got.plan, got.status, got.hours_cap, got.campaign_cap), ("table", "active", 18, 1)
        )
        me = (await self.client.get("/me")).json()["plan"]
        self.assertEqual((me["id"], me["hoursCap"]), ("table", 18))

    async def test_an_unsigned_event_changes_nothing(self) -> None:
        response = await self.send(
            signed=False,
            kind="subscription_started",
            plan="guild",
            period_start=self.now,
            period_end=self.now + DAY,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))

    async def test_the_webhook_needs_no_site_header_but_its_signature(self) -> None:
        # No X-DMbot-Request header and no Origin: the payment company can't send them.
        self.assertEqual((await self.start_table()).status_code, 200)

    async def test_the_same_event_twice_is_applied_once(self) -> None:
        await self.start_table()
        body = json.dumps(
            {
                "id": "evt-x",
                "user_id": ALICE.id,
                "occurred_at": self.now + 5,
                "kind": "extra_hours_bought",
            }
        ).encode()
        headers = {"x-fake-signature": self.provider.sign(body)}
        for _ in range(3):
            await self.client.post("/webhooks/fake", content=body, headers=headers)
        self.assertEqual((await self.plan()).extra_hours, 10)

    async def test_an_older_event_arriving_late_changes_nothing(self) -> None:
        await self.start_table()
        await self.send(kind="subscription_ended", occurred_at=self.now + 100)
        await self.send(kind="payment_failed", occurred_at=self.now + 50)  # delivered late
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("lapsed", None))

    async def test_a_failed_payment_gives_seven_days_then_the_plan_lapses(self) -> None:
        await self.start_table()
        await self.send(kind="payment_failed", occurred_at=self.now + 10)
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("grace", self.now + 10 + 7 * DAY))
        await self.send(kind="payment_failed", occurred_at=self.now + 20)  # retry fails too
        self.assertEqual((await self.plan()).grace_ends_at, self.now + 10 + 7 * DAY)
        await self.send(kind="subscription_ended", occurred_at=self.now + 8 * DAY)
        got = await self.plan()
        self.assertEqual((got.status, got.lapsed_at), ("lapsed", self.now + 8 * DAY))

    async def test_a_renewal_resets_extra_hours_and_keeps_the_plan_change_time(self) -> None:
        await self.start_table()
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 1)
        self.assertEqual((await self.plan()).hours_this_period, 28)
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 30 * DAY,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        got = await self.plan()
        self.assertEqual((got.extra_hours, got.plan_changed_at), (0, self.now))

    async def test_a_plan_change_records_when(self) -> None:
        await self.start_table()
        await self.start_table(plan="guild", occurred_at=self.now + 5)
        got = await self.plan()
        self.assertEqual(
            # When it changed, by DMbot's clock (campaign start times use the same clock).
            (got.plan, got.campaign_cap, got.plan_changed_at),
            ("guild", 5, self.now),
        )

    async def test_unknown_or_unsold_plans_are_refused_and_not_recorded(self) -> None:
        for plan in ("free-forever", "pro", "try-it"):
            self.assertEqual((await self.start_table(plan=plan)).status_code, 422)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM payment_events")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # so a fixed replay can still be applied

    async def test_events_for_people_who_never_signed_in_are_ignored(self) -> None:
        with self.assertLogs("dmbot.web.entitlements_writer", "ERROR"):
            response = await self.start_table(user_id=424242)
        self.assertEqual(response.status_code, 200)

    async def test_an_unreadable_or_oversized_body_is_refused(self) -> None:
        bad = b"not json"
        response = await self.client.post(
            "/webhooks/fake", content=bad, headers={"x-fake-signature": self.provider.sign(bad)}
        )
        self.assertEqual(response.status_code, 400)
        big = b"x" * (64 * 1024 + 1)
        response = await self.client.post(
            "/webhooks/fake", content=big, headers={"x-fake-signature": self.provider.sign(big)}
        )
        self.assertEqual(response.status_code, 413)

    async def test_other_providers_paths_dont_exist(self) -> None:
        response = await self.client.post("/webhooks/paddle", content=b"{}")
        self.assertEqual(response.status_code, 404)

    async def test_an_end_before_its_start_is_retried_not_lost(self) -> None:
        ended = await self.send(kind="subscription_ended", subscription_id="sub_1")
        self.assertEqual(ended.status_code, 503)  # not recorded: the company sends it again
        await self.start_table(occurred_at=self.now - 5)
        body = json.dumps(
            {
                "id": "evt-1",
                "user_id": ALICE.id,
                "occurred_at": self.now,
                "kind": "subscription_ended",
                "subscription_id": "sub_1",
            }
        ).encode()
        again = await self.client.post(
            "/webhooks/fake", content=body, headers={"x-fake-signature": self.provider.sign(body)}
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual((await self.plan()).status, "lapsed")

    async def test_an_old_subscription_ending_doesnt_stop_the_new_one(self) -> None:
        await self.start_table(subscription_id="sub_old")
        await self.start_table(plan="guild", subscription_id="sub_new", occurred_at=self.now + 5)
        await self.send(
            kind="subscription_ended", subscription_id="sub_old", occurred_at=self.now + 9
        )
        got = await self.plan()
        self.assertEqual((got.plan, got.status), ("guild", "active"))

    async def test_the_company_cannot_stop_try_it(self) -> None:
        await self.post("/plan/try-it")
        response = await self.send(kind="subscription_ended", subscription_id="sub_1")
        self.assertEqual(response.status_code, 503)  # a "started" must be on its way
        self.assertEqual((await self.plan()).status, "active")

    async def test_an_end_before_its_start_while_on_try_it_still_stops_the_plan(self) -> None:
        await self.post("/plan/try-it")
        ended = await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        self.assertEqual(ended.status_code, 503)  # not recorded: delivered again later
        await self.start_table(occurred_at=self.now + 5)
        body = json.dumps(
            {
                "id": json.loads(ended.request.content)["id"],  # the same event again
                "user_id": ALICE.id,
                "occurred_at": self.now + 9,
                "kind": "subscription_ended",
                "subscription_id": "sub_1",
            }
        ).encode()
        again = await self.client.post(
            "/webhooks/fake", content=body, headers={"x-fake-signature": self.provider.sign(body)}
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual((await self.plan()).status, "lapsed")

    async def test_a_failure_before_any_plan_is_retried_not_recorded(self) -> None:
        failed = await self.send(kind="payment_failed", subscription_id="sub_1")
        self.assertEqual(failed.status_code, 503)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM payment_events")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_a_renewal_over_a_stopped_duplicate_is_applied(self) -> None:
        # Two checkout tabs: A then B; support cancels B; A keeps renewing.
        await self.start_table(subscription_id="sub_A")
        with self.assertLogs("dmbot.web.entitlements_writer", "WARNING"):
            await self.start_table(subscription_id="sub_B", occurred_at=self.now + 1)
        await self.send(
            kind="subscription_ended", subscription_id="sub_B", occurred_at=self.now + 2
        )
        renewed = await self.start_table(
            kind="subscription_renewed",
            subscription_id="sub_A",
            occurred_at=self.now + 30 * DAY,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        self.assertEqual(renewed.status_code, 200)
        got = await self.plan()
        self.assertEqual((got.status, got.period_start), ("active", self.now + 30 * DAY))

    async def test_try_it_after_a_stopped_plan_forgets_the_old_subscription(self) -> None:
        await self.start_table()
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 1
        )
        self.assertEqual((await self.post("/plan/try-it")).status_code, 204)
        got = await self.plan()
        self.assertEqual((got.plan, got.status), ("try-it", "active"))
        # No billing page for Try It, and the old subscription's late news is ignored.
        portal = await self.post("/billing/portal")
        self.assertEqual(portal.json(), {"error": "no_paid_plan"})
        late = await self.send(kind="payment_failed", subscription_id="sub_1")
        self.assertEqual(late.status_code, 200)
        self.assertEqual((await self.plan()).status, "active")

    async def test_late_news_of_a_stopped_plan_doesnt_overwrite_try_it(self) -> None:
        await self.start_table(subscription_id="sub_1")
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 1
        )
        self.assertEqual((await self.post("/plan/try-it")).status_code, 204)
        for kind in ("subscription_renewed", "subscription_started"):
            late = await self.start_table(
                kind=kind,
                subscription_id="sub_1",
                occurred_at=self.now,  # before the end
                period_start=self.now + 30 * DAY,
                period_end=self.now + 60 * DAY,
            )
            self.assertEqual(late.status_code, 200, kind)
            got = await self.plan()
            self.assertEqual((got.plan, got.status), ("try-it", "active"), kind)
        # A new subscription afterwards still applies.
        await self.start_table(subscription_id="sub_2", occurred_at=self.now + 5)
        self.assertEqual((await self.plan()).plan, "table")

    async def test_a_second_subscription_renewing_is_left_for_a_person(self) -> None:
        await self.start_table(subscription_id="sub_1")
        with self.assertLogs("dmbot.web.entitlements_writer", "ERROR") as logs:
            response = await self.start_table(
                kind="subscription_renewed",
                plan="guild",
                subscription_id="sub_2",
                occurred_at=self.now + 10,
                period_start=self.now + 30 * DAY,
                period_end=self.now + 60 * DAY,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("second subscription", logs.output[0])
        got = await self.plan()
        self.assertEqual((got.plan, got.period_start), ("table", self.now))

    async def test_an_event_dmbot_doesnt_use_is_accepted_and_changes_nothing(self) -> None:
        await self.start_table()
        with self.assertLogs("dmbot.web.payments", "INFO") as logs:
            response = await self.send(kind="refund", occurred_at=self.now + 5)
        self.assertEqual(response.status_code, 200)
        self.assertIn("'refund'", logs.output[0])
        got = await self.plan()
        self.assertEqual((got.plan, got.status), ("table", "active"))

    async def test_a_signature_with_odd_characters_is_refused_not_a_crash(self) -> None:
        body = json.dumps({"id": "e", "user_id": ALICE.id, "occurred_at": 1}).encode()
        for given in ("é" * 64, "\u2603"):
            self.assertFalse(self.provider.verify(body, {"x-fake-signature": given}))
        response = await self.client.post(
            "/webhooks/fake",
            content=body,
            headers={b"x-fake-signature": "é".encode("latin-1") * 64},
        )
        self.assertEqual(response.status_code, 400)

    async def test_extra_hours_are_not_added_to_try_it(self) -> None:
        await self.post("/plan/try-it")
        with self.assertLogs("dmbot.web.entitlements_writer", "ERROR"):
            response = await self.send(kind="extra_hours_bought")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.plan()).extra_hours, 0)

    async def test_the_same_event_three_times_at_once_is_applied_once(self) -> None:
        await self.start_table()
        body = json.dumps(
            {
                "id": "evt-3x",
                "user_id": ALICE.id,
                "occurred_at": self.now + 5,
                "kind": "extra_hours_bought",
            }
        ).encode()
        headers = {"x-fake-signature": self.provider.sign(body)}
        sent = await asyncio.gather(
            *(self.client.post("/webhooks/fake", content=body, headers=headers) for _ in range(3))
        )
        self.assertEqual([r.status_code for r in sent], [200, 200, 200])
        self.assertEqual((await self.plan()).extra_hours, 10)

    async def test_events_after_an_account_is_deleted_are_a_warning(self) -> None:
        await self.start_table()
        await accounts.delete_person(self.db, ALICE.id)  # the real deletion path
        with self.assertLogs("dmbot.web.entitlements_writer", "WARNING") as logs:
            response = await self.send(
                kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 1
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r.levelname for r in logs.records], ["WARNING"])

    async def test_a_deleted_plans_last_events_dont_wait_on_a_new_account(self) -> None:
        # Deletion cancels at the period's end (#435), so the old subscription's last news can
        # come weeks later, after the person has signed up again: it isn't about them now.
        for kind in ("subscription_ended", "payment_failed"):
            with self.subTest(kind=kind):
                await self.start_table()
                await accounts.delete_person(self.db, ALICE.id)
                await self.sign_in()  # the same Discord account, a new DMbot account
                news: dict[str, Any] = {"kind": kind, "subscription_id": "sub_1"}
                news["id"] = f"late-{kind}"
                response = await self.send(**news, occurred_at=self.now + 30 * DAY)
                self.assertEqual(response.status_code, 200)  # not 503: no month of resends
                self.assertIsNone(await entitlements.get(self.db, ALICE.id))
                # Recorded, not just dropped: the company's resend is a duplicate.
                with self.assertLogs("dmbot.web.app", "INFO") as logs:
                    again = await self.send(**news, occurred_at=self.now + 30 * DAY)
                self.assertEqual(again.status_code, 200)
                self.assertTrue(logs.output[-1].endswith(": duplicate"), logs.output)
                if kind == "subscription_ended":  # Try It is once per person, ever
                    # On the Try It they started meanwhile: still not about them.
                    self.assertEqual((await self.post("/plan/try-it")).status_code, 204)
                    news["id"] = f"later-{kind}"
                    response = await self.send(**news, occurred_at=self.now + 31 * DAY)
                    self.assertEqual(response.status_code, 200)
                    got = await self.plan()
                    self.assertEqual((got.plan, got.status), ("try-it", "active"))
                await accounts.delete_person(self.db, ALICE.id)
                await self.sign_in()

    async def test_a_subscription_brought_back_after_deletion_applies_at_renewal(self) -> None:
        # Never stuck: the company undoes the cancel, and its renewal gives the plan back.
        await self.start_table()
        await accounts.delete_person(self.db, ALICE.id)
        await self.sign_in()
        response = await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 30 * DAY,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        self.assertEqual(response.status_code, 200)
        got = await self.plan()
        self.assertEqual((got.plan, got.status), ("table", "active"))

    # Grace only for someone who has paid before (#922)

    async def plan_with_no_payment_on_record(self) -> None:
        """A paid plan whose payment the database has no record of (the writer never makes
        one; rows from before payment kinds were recorded look like this)."""
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, plan_changed_at, provider,"
                " provider_customer_id, provider_subscription_id, last_event_at, updated_at)"
                " VALUES (%(u)s, 'table', 'active', 18, 0, 1, %(s)s, %(e)s, %(s)s, 'fake',"
                " 'cus_1', 'sub_1', %(s)s, %(s)s)",
                {"u": ALICE.id, "s": self.now, "e": self.now + 30 * DAY},
            )

    async def test_a_failed_first_payment_starts_no_plan_and_gives_no_grace(self) -> None:
        response = await self.send(kind="payment_failed", subscription_id="sub_1")
        self.assertEqual(response.status_code, 503)  # nothing to apply it to: retried later
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))
        self.assertIsNone((await self.client.get("/me")).json()["plan"])

    async def test_a_failure_with_no_payment_on_record_gives_no_grace_and_lapses_nothing(
        self,
    ) -> None:
        await self.plan_with_no_payment_on_record()
        for kind in ("payment_failed", "subscription_ended"):
            response = await self.send(kind=kind, subscription_id="sub_1", occurred_at=self.now + 5)
            self.assertEqual(response.status_code, 200)
            got = await self.plan()
            self.assertEqual((got.status, got.grace_ends_at, got.lapsed_at), ("active", None, None))

    async def test_a_failure_recorded_earlier_does_not_count_as_a_payment(self) -> None:
        await self.plan_with_no_payment_on_record()
        for _ in range(2):  # two different failures, then the end: still never paid
            await self.send(
                kind="payment_failed", subscription_id="sub_1", occurred_at=self.now + 5
            )
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at, got.lapsed_at), ("active", None, None))

    async def test_a_record_from_before_kinds_were_kept_counts_as_paid(self) -> None:
        # Rows written before the kind column have no kind: they keep the old behaviour
        # (grace, and lapsing when the plan ends), never a plan that can't lapse.
        await self.plan_with_no_payment_on_record()
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO payment_events (provider, event_id, user_id, subscription_id,"
                " kind, received_at) VALUES ('fake', 'old-1', %s, 'sub_1', NULL, %s)",
                (ALICE.id, self.now),
            )
        await self.send(kind="payment_failed", subscription_id="sub_1", occurred_at=self.now + 5)
        self.assertEqual((await self.plan()).status, "grace")
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        self.assertEqual((await self.plan()).status, "lapsed")

    async def test_extra_hours_count_as_a_payment(self) -> None:
        await self.plan_with_no_payment_on_record()
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 1)
        await self.send(kind="payment_failed", subscription_id="sub_1", occurred_at=self.now + 5)
        self.assertEqual((await self.plan()).status, "grace")

    async def test_a_failed_renewal_after_a_payment_gets_the_grace(self) -> None:
        await self.start_table()
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 30 * DAY,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        await self.send(
            kind="payment_failed", occurred_at=self.now + 60 * DAY, subscription_id="sub_1"
        )
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("grace", self.now + 67 * DAY))

    async def test_a_failure_on_a_second_subscription_after_an_earlier_paid_one_gets_grace(
        self,
    ) -> None:
        await self.start_table()  # paid: sub_1
        await self.send(
            kind="subscription_ended", occurred_at=self.now + 5, subscription_id="sub_1"
        )
        await self.start_table(
            subscription_id="sub_2",
            occurred_at=self.now + 10,
            period_start=self.now + 10,
            period_end=self.now + 40 * DAY,
        )
        await self.send(kind="payment_failed", occurred_at=self.now + 20, subscription_id="sub_2")
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("grace", self.now + 20 + 7 * DAY))

    async def test_a_replayed_failure_changes_nothing_and_each_event_is_recorded_with_its_kind(
        self,
    ) -> None:
        await self.start_table()
        body = json.dumps(
            {
                "id": "evt-fail",
                "user_id": ALICE.id,
                "occurred_at": self.now + 10,
                "kind": "payment_failed",
                "subscription_id": "sub_1",
            }
        ).encode()
        headers = {"x-fake-signature": self.provider.sign(body)}
        codes = [
            (await self.client.post("/webhooks/fake", content=body, headers=headers)).status_code
            for _ in range(3)
        ]
        self.assertEqual(codes, [200, 200, 200])  # a repeat is answered, not an error
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("grace", self.now + 10 + 7 * DAY))
        async with self.db.plan_writer(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT kind, count(*) AS n FROM payment_events WHERE user_id = %s"
                " GROUP BY kind ORDER BY kind",
                (ALICE.id,),
            )
            rows = [(row["kind"], row["n"]) for row in await cur.fetchall()]
        self.assertEqual(rows, [("payment_failed", 1), ("subscription_started", 1)])

    async def test_a_renewal_ends_the_grace(self) -> None:
        await self.start_table()
        await self.send(kind="payment_failed", occurred_at=self.now + 10)
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 20,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("active", None))

    async def test_extra_hours_are_credited_even_after_a_later_renewal(self) -> None:
        await self.start_table()
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 100,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 50)  # late delivery
        self.assertEqual((await self.plan()).extra_hours, 10)

    async def test_extra_hours_need_a_working_plan(self) -> None:
        self.assertEqual((await self.send(kind="extra_hours_bought")).status_code, 200)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))
        await self.start_table()
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 1
        )
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 2)
        self.assertEqual((await self.plan()).extra_hours, 0)

    async def test_two_events_at_once_for_a_new_person_keep_the_newer(self) -> None:
        for _ in range(5):
            await self.reset_plan()
            older = self.start_table(plan="table", occurred_at=self.now)
            newer = self.start_table(plan="guild", occurred_at=self.now + 10)
            await asyncio.gather(older, newer)
            self.assertEqual((await self.plan()).plan, "guild")

    async def test_try_it_and_a_first_payment_at_once_keep_the_payment(self) -> None:
        await asyncio.gather(self.post("/plan/try-it"), self.start_table())
        self.assertEqual((await self.plan()).plan, "table")

    async def test_a_bad_body_is_a_400_not_a_crash(self) -> None:
        not_a_number = {
            "id": "e",
            "user_id": ALICE.id,
            "occurred_at": 1,
            "kind": "subscription_started",
            "plan": "table",
            "period_start": "soon",
            "period_end": 2,
        }
        for body in (b"[1, 2]", b'"text"', json.dumps(not_a_number).encode()):
            response = await self.client.post(
                "/webhooks/fake",
                content=body,
                headers={"x-fake-signature": self.provider.sign(body)},
            )
            self.assertEqual(response.status_code, 400, body)

    async def test_a_streamed_oversized_body_is_cut_off(self) -> None:
        async def chunks() -> Any:
            for _ in range(100):
                yield b"x" * 1024

        response = await self.client.post("/webhooks/fake", content=chunks())
        self.assertEqual(response.status_code, 413)

    async def test_only_the_webhook_skips_the_site_header(self) -> None:
        response = await self.client.post("/plan/try-it")  # cookie, but no header
        self.assertEqual(response.status_code, 403)

    async def reset_plan(self) -> None:
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (ALICE.id,))

    # Try It

    async def test_try_it_starts_once(self) -> None:
        self.assertEqual((await self.post("/plan/try-it")).status_code, 204)
        got = await self.plan()
        self.assertEqual(
            (got.plan, got.hours_cap, got.period_end), ("try-it", 8, self.now + 30 * DAY)
        )
        again = await self.post("/plan/try-it")
        self.assertEqual((again.status_code, again.json()), (409, {"error": "try_it_has_plan"}))

    async def test_try_it_cannot_start_again_after_it_ends(self) -> None:
        await self.post("/plan/try-it")
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', lapsed_at = 1 WHERE user_id = %s",
                (ALICE.id,),
            )
        again = await self.post("/plan/try-it")
        self.assertEqual(again.json(), {"error": "try_it_used"})

    async def test_try_it_is_not_offered_over_a_paid_plan(self) -> None:
        await self.start_table()
        response = await self.post("/plan/try-it")
        self.assertEqual(response.json(), {"error": "try_it_has_plan"})
        self.assertEqual((await self.plan()).plan, "table")

    # Checkout and the billing page

    async def test_checkout_takes_only_a_plan_id_and_the_price_from_plans_json(self) -> None:
        response = await self.post("/billing/checkout", {"plan": "two-tables", "price": 1})
        url = response.json()["url"]
        self.assertEqual(parse_qs(urlsplit(url).query), {"fake_checkout": ["two-tables"]})
        for bad in ({"plan": "pro"}, {"plan": "try-it"}, {"plan": "x"}, {}, [1]):
            response = await self.post("/billing/checkout", bad)
            self.assertEqual(
                (response.status_code, response.json()), (400, {"error": "unknown_plan"})
            )

    async def test_free_access_never_checks_out(self) -> None:
        # #771: someone the free access covers sees no price and can't pay.
        admin = "admin@example.invalid"
        await grants.give(self.db, admin, ALICE.id, "guild", ends_at=None, note="", now=self.now)
        response = await self.post("/billing/checkout", {"plan": "table"})
        self.assertEqual(
            (response.status_code, response.json()), (409, {"error": "has_free_access"})
        )
        # Nor uses up their one Try It (#771).
        trial = await self.post("/plan/try-it")
        self.assertEqual((trial.status_code, trial.json()), (409, {"error": "has_free_access"}))
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))
        await grants.revoke(self.db, "admin@example.invalid", ALICE.id, now=self.now)
        self.assertEqual((await self.post("/billing/checkout", {"plan": "table"})).status_code, 200)

    async def test_the_first_month_of_table_after_try_it_is_discounted_once(self) -> None:
        plain = (await self.post("/billing/checkout", {"plan": "table"})).json()["url"]
        self.assertNotIn("first_month", plain)
        await self.post("/plan/try-it")
        offer = (await self.post("/billing/checkout", {"plan": "table"})).json()["url"]
        self.assertEqual(parse_qs(urlsplit(offer).query)["first_month"], ["199"])
        guild = (await self.post("/billing/checkout", {"plan": "guild"})).json()["url"]
        self.assertNotIn("first_month", guild)
        await self.start_table()  # paid: a working plan refuses a second checkout
        again = await self.post("/billing/checkout", {"plan": "table"})
        self.assertEqual((again.status_code, again.json()), (409, {"error": "has_paid_plan"}))
        await self.send(kind="subscription_ended", subscription_id="sub_1")
        after = (await self.post("/billing/checkout", {"plan": "table"})).json()["url"]
        self.assertNotIn("first_month", after)  # paid before: no second first month

    async def test_the_billing_page_needs_a_paid_plan(self) -> None:
        none = await self.post("/billing/portal")
        self.assertEqual((none.status_code, none.json()), (409, {"error": "no_paid_plan"}))
        await self.start_table()
        self.assertIn("fake_billing", (await self.post("/billing/portal")).json()["url"])

    async def test_buying_needs_a_session_and_the_site_header(self) -> None:
        self.assertEqual(
            (await self.client.post("/billing/checkout", json={"plan": "table"})).status_code, 403
        )
        self.client.cookies.clear()
        self.assertEqual((await self.post("/billing/checkout", {"plan": "table"})).status_code, 401)


class NoPaymentCompanyYet(DatabaseTest):
    async def test_buying_says_payments_are_off(self) -> None:
        app = create_app(settings(), self.db, FakeDiscord(), clock=lambda: 1_800_000_000)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API) as c:
            start = await c.get("/auth/discord/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            await c.get("/auth/discord/callback", params={"state": state, "code": "good-code"})
            response = await c.post(
                "/billing/checkout", headers={"X-DMbot-Request": "1"}, json={"plan": "table"}
            )
            self.assertEqual(
                (response.status_code, response.json()), (503, {"error": "payments_off"})
            )
            self.assertEqual((await c.post("/webhooks/fake", content=b"{}")).status_code, 404)


class WriterDirectly(DatabaseTest):
    async def test_an_event_without_a_period_is_ignored(self) -> None:
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, created_at, last_sign_in_at) VALUES (%s, 0, 0)",
                (ALICE.id,),
            )
        event = PaymentEvent(
            provider="fake",
            event_id="e",
            kind="subscription_started",
            user_id=ALICE.id,
            occurred_at=1,
            plan="table",
        )
        self.assertEqual(await entitlements_writer.apply_event(self.db, event, now=1), "rejected")


class PaymentSettings(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        "DATABASE_URL": "postgresql://x",
        "DISCORD_CLIENT_ID": "1",
        "DISCORD_CLIENT_SECRET": "s",
        "WEB_SECRET_KEY": "k" * 40,
        "WEB_SITE_URL": "http://localhost:4321",
        "WEB_API_URL": "http://localhost:8080",
    }

    def test_no_payment_company_by_default(self) -> None:
        self.assertEqual(load_web_settings(self.ENV).payment_provider, "")

    def test_the_fake_company_only_on_localhost_and_with_a_secret(self) -> None:
        fake = {**self.ENV, "PAYMENT_PROVIDER": "fake", "PAYMENT_WEBHOOK_SECRET": "s" * 20}
        self.assertEqual(load_web_settings(fake).payment_provider, "fake")
        with self.assertRaisesRegex(ConfigError, "only for testing on localhost"):
            load_web_settings({**fake, "WEB_API_URL": "https://api.dmbot.example"})
        with self.assertRaisesRegex(ConfigError, "PAYMENT_WEBHOOK_SECRET"):
            load_web_settings({**fake, "PAYMENT_WEBHOOK_SECRET": ""})

    def test_companies_not_built_yet_are_refused(self) -> None:
        with self.assertRaisesRegex(ConfigError, "isn't built yet"):
            load_web_settings({**self.ENV, "PAYMENT_PROVIDER": "paddle"})
