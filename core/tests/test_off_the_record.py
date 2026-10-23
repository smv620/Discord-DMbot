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


class TheRestart(unittest.IsolatedAsyncioTestCase):
    """A restart that resumes the session keeps the pauses (#2466): saved with the session, put back
    before ears hears anyone, their timers armed for the time left."""

    async def asyncSetUp(self) -> None:
        self.clock = Clock()
        self.wall = Clock()
        self.wall.now = 50_000.0
        self.said: list[str] = []
        self.saved: list[dict[str, Any]] = []

        def build() -> tuple[ConsentStore, OffTheRecord]:
            consent = ConsentStore(cast(Any, None), clock=self.clock, wall=self.wall)
            consent._store(G, frozenset({ALICE, BOB, DM}))

            async def push(guild: int) -> None:
                return None

            async def say(guild: int, text: str) -> bool:
                self.said.append(text)
                return True

            async def save(guild: int, paused: dict[str, Any]) -> None:
                self.saved.append(paused)

            return consent, OffTheRecord(
                consent,
                push=push,
                say=say,
                name_of=lambda guild, user: {ALICE: "Alice", BOB: "Bob", DM: "Dana"}[user],
                listening=lambda guild: True,
                save=save,
                wall=self.wall,
            )

        self.build = build

    async def test_pauses_are_saved_and_come_back_with_the_time_left(self) -> None:
        _, otr = self.build()
        await otr.pause(G, ALICE, 15)
        await otr.pause(G, BOB, None)
        await otr.pause_table(G, 5)
        saved = self.saved[-1]
        self.assertEqual(saved["people"], {str(ALICE): 50_900, str(BOB): None})
        self.assertEqual(saved["table"], 50_300)
        otr.end_session(G)  # (the old process is gone)
        self.clock.now += 100  # a restart takes a while
        self.wall.now += 100
        consent2, otr2 = self.build()
        await otr2.restore(G, saved)
        self.assertEqual(consent2.consenting_now(G), frozenset())  # the table's break is on
        self.assertTrue(consent2.table_paused(G))
        self.assertTrue(consent2.is_paused(G, ALICE))
        self.assertTrue(consent2.is_paused(G, BOB))
        self.assertEqual(consent2.paused_until(G, ALICE), self.clock.now + 800)  # 15 min - 100 s
        self.assertEqual(set(otr2._timers), {(G, ALICE), (G, 0)})  # a timer for each with an end
        otr2.end_session(G)

    async def test_a_pause_that_ran_out_while_down_is_not_restored_and_the_table_is_told(
        self,
    ) -> None:
        _, otr = self.build()
        await otr.pause(G, ALICE, 5)
        saved = self.saved[-1]
        otr.end_session(G)
        self.wall.now += 600
        self.clock.now += 600
        consent2, otr2 = self.build()
        await otr2.restore(G, saved)
        self.assertTrue(consent2.has_consent(G, ALICE))  # recorded again: it ended
        await otr2.say_expired(G, saved)
        self.assertEqual(self.said[-1], said_back("Alice"))

    async def test_a_person_who_stopped_is_not_restored_and_a_stop_cancels_the_timer(self) -> None:
        consent, otr = self.build()
        await otr.pause(G, ALICE, 5)
        saved = self.saved[-1]
        otr.forget(G, ALICE)  # Stop recording me
        consent.stop_now(G, ALICE)
        self.assertEqual(otr._timers, {})
        said = len(self.said)
        self.clock.now += 400
        await asyncio.sleep(0)
        self.assertEqual(len(self.said), said)  # nobody says she is back on the record
        consent3 = ConsentStore(cast(Any, None), clock=self.clock, wall=self.wall)
        consent3._store(G, frozenset({BOB, DM}))  # she is not in the list any more
        self.assertEqual(consent3.restore(G, saved), [])

    async def test_the_timer_that_fires_a_hair_early_still_resumes(self) -> None:
        consent, otr = self.build()
        await otr.pause(G, ALICE, 5)
        self.clock.now += 299.9999  # the sleep returned just before the deadline
        await otr._after((G, ALICE), 0)
        self.assertTrue(consent.has_consent(G, ALICE))
        self.assertEqual(self.said[-1], said_back("Alice"))


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

    async def test_two_changes_at_once_never_save_the_older_state_last(self) -> None:
        saved: list[dict[str, Any]] = []
        first = True

        async def save(guild: int, snapshot: dict[str, Any]) -> None:
            nonlocal first
            if first:  # the first save is slow; the second change comes in meanwhile
                first = False
                await asyncio.sleep(0.05)
            saved.append(snapshot)

        self.otr._save = save
        await asyncio.gather(self.otr.pause(G, ALICE, None), self.otr.pause(G, BOB, None))
        self.assertEqual(set(saved[-1]["people"]), {str(ALICE), str(BOB)})

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

    async def test_a_failing_ears_or_chat_never_undoes_a_pause_or_throws(self) -> None:
        async def broken_push(guild: int) -> None:
            raise RuntimeError("ears down")

        async def broken_say(guild: int, text: str) -> bool:
            raise RuntimeError("chat down")

        self.otr._push = broken_push
        self.otr._say = broken_say
        done = await self.otr.pause(G, ALICE, 5)
        self.assertTrue(done.ok)
        self.assertFalse(done.told)  # the pauser is told nobody could be told
        self.assertFalse(self.consent.has_consent(G, ALICE))  # the pause holds in core anyway
        self.assertTrue(await self.otr.resume(G, ALICE))  # and so does the resume
        self.assertTrue(self.consent.has_consent(G, ALICE))

    async def test_a_persons_pause_ending_during_the_tables_break_says_nothing(self) -> None:
        await self.otr.pause(G, ALICE, 5)
        await self.otr.pause_table(G, None)
        said = len(self.said)
        await self.otr.resume(G, ALICE)
        self.assertEqual(len(self.said), said)  # still on the break: no "back on the record"
        self.assertEqual(self.consent.consenting_now(G), frozenset())

    async def test_resume_after_the_time_ran_out_still_sends_the_list(self) -> None:
        await self.otr.pause(G, ALICE, 5)
        self.clock.now += 400  # expired, but the timer has not fired yet
        pushed = len(self.pushed)
        self.assertTrue(await self.otr.resume(G, ALICE))
        self.assertEqual(len(self.pushed), pushed + 1)

    async def test_the_words_say_the_yes_stays_and_the_table_line_is_short(self) -> None:
        self.assertIn("Your yes stays as it is", paused_text(5300))
        self.assertIn("Your yes stays as it is", paused_text(None))
        self.assertEqual(said_off("Alice", 5), "⏸️ Alice is off the record for 5 minutes.")
        self.assertEqual(said_off("Alice", None), "⏸️ Alice is off the record until they resume.")

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
