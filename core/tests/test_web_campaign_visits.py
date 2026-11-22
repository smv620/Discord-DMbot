"""The account page's order of campaigns (#5037, migration 0596): each campaign carries the latest
of the person's own last visit, its last session and when it was made (`lastAccessedAt`); opening a
campaign's pages moves it up, a visit is written once a minute at most, and a campaign outside the
person's servers records nothing. A time only. Needs Postgres."""

from __future__ import annotations

import time
import unittest
from datetime import datetime

from psycopg.types.json import Jsonb

from dmbot.campaigns import CampaignStore
from dmbot.web.me import _last_accessed
from tests.test_web_api import ALICE, THURSDAY, WebApi

REQ = {"X-DMbot-Request": "1"}  # (the site asks every change this way)


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class LastAccessed(WebApi):
    async def test_a_campaign_not_yet_opened_is_as_old_as_its_creation(self) -> None:
        store = CampaignStore(self.db, clock=lambda: self.now)
        made = await store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        await self.sign_in()
        (got,) = (await self.client.get("/me")).json()["campaigns"]
        self.assertEqual(got["id"], made.id)
        self.assertEqual(int(_when(got["lastAccessedAt"]).timestamp()), self.now)

    async def test_opening_a_campaign_moves_it_up_once_a_minute_at_most(self) -> None:
        self.now = int(time.time()) - 3600  # (made an hour ago, as the real clock goes)
        store = CampaignStore(self.db, clock=lambda: self.now)
        made = await store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        await self.sign_in()
        ref = f"{THURSDAY.id}.{made.id}"
        before = (await self.client.get("/me")).json()["campaigns"][0]["lastAccessedAt"]
        self.assertEqual(
            (await self.client.post(f"/me/campaigns/{ref}/visit", headers=REQ)).status_code, 204
        )
        after = (await self.client.get("/me")).json()["campaigns"][0]["lastAccessedAt"]
        self.assertGreater(_when(after), _when(before))
        await self.client.post(
            f"/me/campaigns/{ref}/visit", headers=REQ
        )  # (inside the minute: not written)
        again = (await self.client.get("/me")).json()["campaigns"][0]["lastAccessedAt"]
        self.assertEqual(again, after)

    async def test_a_campaign_outside_the_persons_servers_records_nothing(self) -> None:
        store = CampaignStore(self.db, clock=lambda: self.now)
        lost = await store.create(444, "Not In My Servers", ALICE.id)
        await self.sign_in()
        ref = f"444.{lost.id}"
        self.assertEqual(
            (await self.client.post(f"/me/campaigns/{ref}/visit", headers=REQ)).status_code, 204
        )
        got = (await self.client.get("/me")).json()["campaigns"][0]
        self.assertIsNone(got["lastAccessedAt"])  # (nothing known of it, and no visit written)
        # (and a made-up reference is no error the page must show)
        for bad in ("nope", f"{THURSDAY.id}.{'0' * 32}"):
            self.assertEqual(
                (await self.client.post(f"/me/campaigns/{bad}/visit", headers=REQ)).status_code, 204
            )


class TheVisits(WebApi):
    async def stored(self) -> dict[str, int]:
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT campaign_visits FROM web_users WHERE user_id = %s", (ALICE.id,)
            )
            return dict((await cur.fetchone() or {}).get("campaign_visits") or {})

    async def test_nothing_is_written_for_a_campaign_that_does_not_exist_and_old_ones_go(
        self,
    ) -> None:
        self.now = int(time.time()) - 3600
        store = CampaignStore(self.db, clock=lambda: self.now)
        made = await store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        await self.sign_in()
        ghost = f"{THURSDAY.id}.{'0' * 32}"
        await self.client.post(f"/me/campaigns/{ghost}/visit", headers=REQ)
        self.assertEqual(await self.stored(), {})
        # an old entry (and one that is not a time) is dropped when the next visit is written
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "UPDATE web_users SET campaign_visits = %s WHERE user_id = %s",
                (Jsonb({"1.old": 1_000, "2.odd": "soon"}), ALICE.id),
            )
        await self.client.post(f"/me/campaigns/{THURSDAY.id}.{made.id}/visit", headers=REQ)
        self.assertEqual(list(await self.stored()), [f"{THURSDAY.id}.{made.id}"])

    async def test_the_export_carries_the_times(self) -> None:
        self.now = int(time.time()) - 3600
        store = CampaignStore(self.db, clock=lambda: self.now)
        made = await store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        await self.sign_in()
        await self.client.post(f"/me/campaigns/{THURSDAY.id}.{made.id}/visit", headers=REQ)
        got = (await self.client.get("/me/export")).json()
        self.assertEqual(list(got["account"]["campaignVisits"]), [f"{THURSDAY.id}.{made.id}"])


class TheOrder(unittest.TestCase):
    """The pure rule: the latest of the three, and nothing when none is known."""

    def test_the_latest_of_the_visit_the_last_session_and_the_creation(self) -> None:
        self.assertEqual(
            _last_accessed(1_800_000_000, "2026-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            "2027-01-15T08:00:00Z",
        )
        self.assertEqual(
            _last_accessed(None, "2026-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            "2026-01-01T00:00:00Z",
        )
        self.assertEqual(_last_accessed(None, None, "2025-01-01T00:00:00Z"), "2025-01-01T00:00:00Z")
        self.assertIsNone(_last_accessed(None, None, None))
