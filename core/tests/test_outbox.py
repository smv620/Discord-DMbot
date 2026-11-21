"""The outbox (#3843): a row is delivered once, tried again after a failure, never claimed by two
bots at once, never made by a rolled-back transaction, never carries words, and never crosses
servers. Database tests: skipped where there is no Postgres."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from dmbot.campaigns.store import CampaignStore
from dmbot.outbox import MAX_TRIES, STALE_S, NotAnId, Outbox, clean_payload
from dmbot.outbox_delivery import deliver_round
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_700_000_000.0
        self.box = Outbox(self.db, clock=lambda: self.now)
        campaigns = CampaignStore(self.db)
        self.one = await campaigns.create(1, "One", fx.DM)
        self.two = await campaigns.create(2, "Two", fx.DM)
        self.said: list[tuple[int, str, Mapping[str, Any]]] = []

    async def say(self, gid: int, cid: str, payload: Mapping[str, Any]) -> None:
        self.said.append((gid, cid, payload))

    async def state(self, gid: int, row_id: int) -> tuple[str, int]:
        async with self.db.guild(gid) as conn:
            cur = await conn.execute(
                "SELECT state, tries FROM outbox WHERE guild_id = %s AND id = %s", (gid, row_id)
            )
            row = await cur.fetchone()
        assert row is not None
        return row["state"], int(row["tries"])


class ThePayload(Base):
    """What may be written into a row."""

    def test_ids_counts_and_fixed_words_pass(self) -> None:
        self.assertEqual(
            clean_payload({"session_at": 100, "ok": True, "kind": "recap_ready"}),
            {"session_at": 100, "ok": True, "kind": "recap_ready"},
        )

    def test_a_sentence_a_name_or_a_nested_value_is_refused(self) -> None:
        for bad in ("The Silver Lock", "Mira drew her blade.", "x" * 65, "", ["a"], {"a": 1}, 1.5):
            with self.assertRaises(NotAnId, msg=repr(bad)):
                clean_payload({"title": bad})
        with self.assertRaises(NotAnId):
            clean_payload({"a title": 1})

    async def test_a_planted_sentence_never_reaches_the_table(self) -> None:
        with self.assertRaises(NotAnId):
            await self.box.add_now(1, self.one.id, "recap_ready", {"note": "the secret door"})
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM outbox")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class TheDelivery(Base):
    async def test_a_row_is_delivered_once(self) -> None:
        await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": 100})
        handlers = {"recap_ready": self.say}
        self.assertEqual(await deliver_round(self.box, 1, handlers), 1)
        self.assertEqual(await deliver_round(self.box, 1, handlers), 0)
        self.assertEqual(self.said, [(1, self.one.id, {"session_at": 100})])

    async def test_a_failed_delivery_is_tried_again_then_ends_failed(self) -> None:
        row = await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": 100})

        async def refuse(gid: int, cid: str, payload: Mapping[str, Any]) -> None:
            raise RuntimeError("not-posted")

        for _ in range(MAX_TRIES):
            self.assertEqual(await deliver_round(self.box, 1, {"recap_ready": refuse}), 0)
        self.assertEqual(await self.state(1, row), ("failed", MAX_TRIES))
        self.assertEqual(await deliver_round(self.box, 1, {"recap_ready": self.say}), 0)

    async def test_it_goes_through_after_one_failure(self) -> None:
        row = await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": 100})
        calls: list[int] = []

        async def flaky(gid: int, cid: str, payload: Mapping[str, Any]) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("busy")

        await deliver_round(self.box, 1, {"recap_ready": flaky})
        self.assertEqual(await self.state(1, row), ("waiting", 1))
        await deliver_round(self.box, 1, {"recap_ready": flaky})
        self.assertEqual(await self.state(1, row), ("sent", 2))

    async def test_a_kind_nobody_handles_ends_at_once(self) -> None:
        row = await self.box.add_now(1, self.one.id, "something_new", {})
        await deliver_round(self.box, 1, {"recap_ready": self.say})
        self.assertEqual(await self.state(1, row), ("failed", 1))

    async def test_another_servers_row_is_never_delivered_here(self) -> None:
        await self.box.add_now(2, self.two.id, "recap_ready", {"session_at": 5})
        self.assertEqual(await deliver_round(self.box, 1, {"recap_ready": self.say}), 0)
        self.assertEqual(self.said, [])
        self.assertEqual(await deliver_round(self.box, 2, {"recap_ready": self.say}), 1)


class TheClaim(Base):
    async def test_two_bots_never_claim_one_row(self) -> None:
        for n in range(6):
            await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": n})
        a, b = await asyncio.gather(self.box.claim(1, 6), self.box.claim(1, 6))
        ids = [r.id for r in (*a, *b)]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)

    async def test_a_fresh_claim_is_left_alone_and_a_stale_one_is_taken_again(self) -> None:
        await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": 1})
        (first,) = await self.box.claim(1)
        self.assertEqual(await self.box.claim(1), [])
        self.now += STALE_S - 1
        self.assertEqual(await self.box.claim(1), [])
        self.now += 2  # (the bot that held it died)
        (again,) = await self.box.claim(1)
        self.assertEqual((again.id, again.tries), (first.id, 2))

    async def test_a_rolled_back_transaction_leaves_no_row(self) -> None:
        with self.assertRaises(RuntimeError):
            async with self.db.guild(1) as conn:
                await self.box.add(conn, 1, self.one.id, "recap_ready", {"session_at": 1})
                raise RuntimeError("the work failed")
        self.assertEqual(await self.box.claim(1), [])

    async def test_old_finished_rows_are_swept_and_waiting_ones_stay(self) -> None:
        done = await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": 1})
        await self.box.add_now(1, self.one.id, "recap_ready", {"session_at": 2})
        await self.box.claim(1, 1)
        await self.box.sent(1, done)
        self.now += 4 * 24 * 3600
        self.assertEqual(await self.box.sweep(1), 1)
        self.assertEqual(len(await self.box.claim(1)), 1)
