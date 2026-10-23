"""Off the record (#2466): a person's pause and the DM's table pause take people out of every
consent check, end by themselves, and leave consent itself alone. Pure: a fake clock and fakes
for ears, the chat and names."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, cast

from dmbot.consent import ConsentStore
from dmbot.off_the_record import (
    TABLE_BACK,
    TABLE_OFF,
    OffTheRecord,
    paused_text,
    said_back,
    said_off,
)

G, ALICE, BOB, DM = 1, 11, 12, 99


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def store(clock: Clock) -> ConsentStore:
    consent = ConsentStore(cast(Any, None), clock=clock)
    consent._store(G, frozenset({ALICE, BOB, DM}))
    return consent


class ThePause(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.consent = store(self.clock)

    def test_a_paused_person_is_off_every_consent_check_and_still_agreed(self) -> None:
        self.consent.pause(G, ALICE, 300)
        self.assertFalse(self.consent.has_consent(G, ALICE))
        self.assertTrue(self.consent.has_consent(G, BOB))
        self.assertEqual(self.consent.consenting_now(G), frozenset({BOB, DM}))
        self.assertTrue(self.consent.agreed_now(G, ALICE))  # consent itself is untouched
        self.assertIn(ALICE, self.consent.agreed_set(G))

    def test_it_ends_by_itself_and_until_resumed_does_not(self) -> None:
        self.consent.pause(G, ALICE, 300)
        self.consent.pause(G, BOB, None)
        self.clock.now += 299
        self.assertTrue(self.consent.is_paused(G, ALICE))
        self.clock.now += 2
        self.assertFalse(self.consent.is_paused(G, ALICE))
        self.assertTrue(self.consent.has_consent(G, ALICE))
        self.clock.now += 86400
        self.assertTrue(self.consent.is_paused(G, BOB))
        self.assertTrue(self.consent.resume(G, BOB))
        self.assertFalse(self.consent.resume(G, BOB))
        self.assertTrue(self.consent.has_consent(G, BOB))

    def test_only_someone_who_agreed_can_be_paused_and_a_pause_grants_nothing(self) -> None:
        self.consent.pause(G, 555, 300)  # never agreed
        self.assertFalse(self.consent.is_paused(G, 555))
        self.assertFalse(self.consent.has_consent(G, 555))

    def test_the_table_pause_empties_the_list_and_puts_back_exactly_the_agreed(self) -> None:
        self.consent.pause(G, ALICE, None)
        self.consent.pause_table(G, 300)
        self.assertEqual(self.consent.consenting_now(G), frozenset())
        self.assertFalse(self.consent.has_consent(G, DM))
        self.clock.now += 301
        # the table is back; Alice's own pause is still hers
        self.assertEqual(self.consent.consenting_now(G), frozenset({BOB, DM}))

    def test_the_session_ending_ends_every_pause_and_a_stop_still_wins(self) -> None:
        self.consent.pause(G, ALICE, None)
        self.consent.pause_table(G, None)
        self.consent.end_session(G)
        self.assertEqual(self.consent.consenting_now(G), frozenset({ALICE, BOB, DM}))
        self.consent.stop_now(G, BOB)  # a stop is not undone by a pause ending
        self.consent.pause(G, ALICE, 60)
        self.consent.end_session(G)
        self.assertFalse(self.consent.has_consent(G, BOB))

    def test_another_server_is_not_touched(self) -> None:
        self.consent._store(2, frozenset({ALICE}))
        self.consent.pause(G, ALICE, None)
        self.assertTrue(self.consent.has_consent(2, ALICE))


class TheService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = Clock()
        self.consent = store(self.clock)
        self.pushed: list[frozenset[int]] = []
        self.said: list[str] = []
        self.dropped: list[tuple[int, int | None]] = []
        self.listening = True

        async def push(guild: int) -> None:
            self.pushed.append(await self.consent.consenting(guild))

        async def say(guild: int, text: str) -> bool:
            self.said.append(text)
            return True

        self.otr = OffTheRecord(
            self.consent,
            push=push,
            say=say,
            name_of=lambda guild, user: {ALICE: "Alice", BOB: "Bob", DM: "Dana"}[user],
            listening=lambda guild: self.listening,
            drop=lambda guild, user: self.dropped.append((guild, user)),
            wall=lambda: 5000.0,
        )

    async def test_a_pause_drops_the_person_from_ears_and_tells_the_table(self) -> None:
        done = await self.otr.pause(G, ALICE, 5)
        self.assertTrue(done.ok)
        self.assertEqual(done.until, 5300)
        self.assertEqual(self.pushed[-1], frozenset({BOB, DM}))  # off the allowlist
        self.assertEqual(self.said, [said_off("Alice", 5)])
        self.assertEqual(self.dropped, [(G, ALICE)])  # what she was mid-saying is thrown away
        self.assertIn("<t:5300:R>", paused_text(done.until))

    async def test_resume_now_puts_them_back_and_says_so(self) -> None:
        await self.otr.pause(G, ALICE, None)
        self.assertTrue(await self.otr.resume(G, ALICE))
        self.assertEqual(self.pushed[-1], frozenset({ALICE, BOB, DM}))
        self.assertEqual(self.said[-1], said_back("Alice"))
        self.assertFalse(await self.otr.resume(G, ALICE))  # nothing to resume now

    async def test_the_timer_puts_them_back_by_itself(self) -> None:
        self.consent.pause(G, ALICE, 0.01)
        self.otr._arm((G, ALICE), None)
        task = asyncio.create_task(self.otr._after((G, ALICE), 0.01))
        self.clock.now += 5
        await task
        self.assertEqual(self.said, [said_back("Alice")])
        self.assertEqual(self.pushed[-1], frozenset({ALICE, BOB, DM}))

    async def test_no_pause_when_not_listening_or_when_not_agreed_or_for_a_strange_length(
        self,
    ) -> None:
        self.listening = False
        self.assertFalse((await self.otr.pause(G, ALICE, 5)).ok)
        self.listening = True
        self.assertFalse((await self.otr.pause(G, 555, 5)).ok)
        self.assertFalse((await self.otr.pause(G, ALICE, 9999)).ok)
        self.assertFalse((await self.otr.pause(G, ALICE, 0)).ok)
        self.assertEqual(self.said, [])
        self.assertTrue(self.consent.has_consent(G, ALICE))

    async def test_the_table_pause_sends_an_empty_allowlist_then_exactly_the_agreed(self) -> None:
        self.assertTrue((await self.otr.pause_table(G, 15)).ok)
        self.assertEqual(self.pushed[-1], frozenset())
        self.assertEqual(self.said[-1], TABLE_OFF)
        self.assertEqual(self.dropped[-1], (G, None))
        self.assertTrue(await self.otr.resume_table(G))
        self.assertEqual(self.pushed[-1], frozenset({ALICE, BOB, DM}))
        self.assertEqual(self.said[-1], TABLE_BACK)

    async def test_a_session_end_is_quiet_and_clears_everything(self) -> None:
        await self.otr.pause(G, ALICE, 15)
        await self.otr.pause_table(G, None)
        said = len(self.said)
        self.otr.end_session(G)
        self.assertEqual(len(self.said), said)
        self.assertEqual(self.consent.consenting_now(G), frozenset({ALICE, BOB, DM}))
        self.assertEqual(self.otr._timers, {})

    async def test_a_second_pause_replaces_the_first(self) -> None:
        await self.otr.pause(G, ALICE, 5)
        first = self.otr._timers[(G, ALICE)]
        await self.otr.pause(G, ALICE, 15)
        await asyncio.sleep(0)
        self.assertTrue(first.cancelled() or first.done())
        self.assertIsNot(self.otr._timers[(G, ALICE)], first)
        self.otr.end_session(G)


if __name__ == "__main__":
    unittest.main()
