"""A plot thread's state from the website (#3696 slice 2a, migration 0580): only a DM queues it, the
bot runs the card's own store calls (`set_condition`, `check_thread`), only a confirmed thread of
the campaign can be moved (a character's id must never reach `set_condition`), and a thread that
cannot be checked is `gone`. Postgres. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import threads
from dmbot.memory import web_actions as wa
from dmbot.memory.models import OPEN, RESOLVED
from dmbot.web import plot_threads, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web


class ThreadWeb(Web):
    """A DM's campaign with one quest, and the bot's loop over the queue (no tests of its own)."""

    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        made = await self.memory.new_thread(
            self.g, self.one.id, "The drowned bell", "quest", OPEN, source="dm"
        )
        self.bell = made.value.id

    async def bot_loop(self) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(self.memory, self.g, {ALICE.id}, action, actions=self.actions)
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def press(self, thread: str, state: str, who: Any = None) -> str:
        return await plot_threads.queue_state(
            self.web, who or self.session, self.cref, {"thread": thread, "state": state}
        )

    async def now(self, thread: str) -> list[str]:
        rows = await self.memory.state_history(self.g, self.one.id, thread)
        return [r.condition for r in rows if r.closed_at is None]


class ChangingAThreadsState(ThreadWeb):
    async def test_a_state_change_is_the_cards_own_store_call(self) -> None:
        await self.press(self.bell, "active")
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertEqual(await self.now(self.bell), ["active"])
        await self.press(self.bell, "resolved")
        await self.bot_loop()
        self.assertEqual(await self.now(self.bell), [RESOLVED])

    async def test_the_state_it_is_already_in_counts_as_checked(self) -> None:
        before = len(await self.memory.state_history(self.g, self.one.id, self.bell))
        await self.press(self.bell, "open")
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        after = await self.memory.state_history(self.g, self.one.id, self.bell)
        self.assertEqual(len(after), before + 1)  # (the check wrote a dated row)
        self.assertEqual(await self.now(self.bell), [OPEN])

    async def test_checked_still_going_writes_a_dated_row_and_keeps_the_state(self) -> None:
        before = len(await self.memory.state_history(self.g, self.one.id, self.bell))
        await self.press(self.bell, "check")
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        after = await self.memory.state_history(self.g, self.one.id, self.bell)
        self.assertEqual(len(after), before + 1)
        self.assertEqual(await self.now(self.bell), [OPEN])

    async def test_a_resolved_thread_cannot_be_checked(self) -> None:
        await self.press(self.bell, "resolved")
        await self.press(self.bell, "check")
        first, second = await self.bot_loop()
        self.assertFalse(first.error)
        self.assertEqual(second.error, wa.GONE)

    async def test_a_character_is_never_moved_by_a_thread_press(self) -> None:
        tavin = await self.add("Tavin", "character", role="player_character")
        await self.press(tavin, "active")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)
        self.assertEqual(await self.now(tavin), [])

    async def test_a_made_up_thread_is_gone(self) -> None:
        await self.press("a" * 32, "active")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_a_thread_that_is_not_confirmed_is_gone(self) -> None:
        guess = await self.add("A guess", "thread")
        await self.sql("UPDATE memory_entities SET status = 'proposed' WHERE id = %s", guess)
        await self.press(guess, "active")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_another_campaigns_thread_is_gone(self) -> None:
        made = await self.memory.new_thread(
            self.g, self.two.id, "Their bell", "quest", OPEN, source="dm"
        )
        await self.press(made.value.id, "active")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)
        rows = await self.memory.state_history(self.g, self.two.id, made.value.id)
        self.assertEqual([r.condition for r in rows if r.closed_at is None], ["open"])

    async def test_a_merged_away_thread_is_gone(self) -> None:
        old = await self.add("Old bell", "quest")
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            self.bell,
            old,
        )
        await self.press(old, "active")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_a_state_the_door_never_lets_in_is_still_refused_by_the_bot(self) -> None:
        # (apply() is the backstop if a queued row is ever odd: the store refuses the word)
        row = wa.Action(
            id="x" * 32, campaign_id=self.one.id, entity_id="", user_id=ALICE.id,
            action="thread-state", text=f"{self.bell} bogus",
        )  # fmt: skip
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, row, actions=self.actions)
        self.assertEqual(outcome.error, wa.GONE)

    async def test_only_a_dm_queues_it(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await self.press(self.bell, "active", who=bob)
        self.assertEqual(err.exception.code, "not_allowed")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        for body in (
            {"thread": "nope", "state": "active"}, {"thread": self.bell, "state": "done"},
            {"thread": self.bell}, {"state": "active"}, 5,
        ):  # fmt: skip
            with self.assertRaises(RecapError) as err:
                await plot_threads.queue_state(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    def test_every_state_the_page_may_send_is_a_card_state(self) -> None:
        self.assertEqual(threads.ALL_STATES, ("hinted", "open", "active", "resolved", "abandoned"))


class AddingWhoIsInvolved(ThreadWeb):
    """Slice 2b (migration 0581): the card's `involve` from the page."""

    async def involve(self, thread: str, who: str, role: str = "helper", user: Any = None) -> str:
        return await plot_threads.queue_involve(
            self.web, user or self.session, self.cref,
            {"thread": thread, "who": who, "role": role},
        )  # fmt: skip

    async def involved(self, thread: str) -> list[tuple[str, str]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT subject_id, detail FROM memory_relations WHERE predicate = 'in_thread'"
                " AND object_id = %s AND status = 'confirmed'", (thread,),
            )  # fmt: skip
            return [(str(r["subject_id"]), str(r["detail"])) for r in await cur.fetchall()]

    async def test_a_name_is_added_with_its_part(self) -> None:
        mira = await self.add("Mira", "character")
        await self.involve(self.bell, mira, "  quest   giver ")
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertEqual(await self.involved(self.bell), [(mira, "quest giver")])

    async def test_a_part_may_be_left_empty(self) -> None:
        keep = await self.add("Greywater Keep", "place")
        for role in ("", "   "):
            await self.involve(self.bell, keep, role)
        outcomes = await self.bot_loop()
        self.assertEqual([bool(o.error) for o in outcomes], [False, False])
        self.assertEqual(await self.involved(self.bell), [(keep, "")])

    async def test_another_thread_the_thread_itself_or_a_made_up_id_is_gone(self) -> None:
        other = await self.add("The missing heir", "mystery")
        for who in (other, "a" * 32):
            await self.involve(self.bell, who)
        outcomes = await self.bot_loop()
        self.assertEqual([o.error for o in outcomes], [wa.GONE] * 2)
        self.assertEqual(await self.involved(self.bell), [])

    async def test_a_character_is_not_a_thread_to_add_people_to(self) -> None:
        mira, tavin = await self.add("Mira", "character"), await self.add("Tavin", "character")
        await self.involve(mira, tavin)
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_another_campaigns_thread_is_gone(self) -> None:
        theirs = await self.memory.new_thread(
            self.g, self.two.id, "Their bell", "quest", OPEN, source="dm"
        )
        mira = await self.add("Mira", "character")
        await self.involve(theirs.value.id, mira)
        outcomes = await self.bot_loop()
        self.assertEqual([o.error for o in outcomes], [wa.GONE])

    async def test_only_a_dm_queues_it_and_a_bad_body_is_refused(self) -> None:
        mira = await self.add("Mira", "character")
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await self.involve(self.bell, mira, user=bob)
        self.assertEqual(err.exception.code, "not_allowed")
        for body in (
            {"thread": self.bell, "who": mira, "role": "x" * 61},
            {"thread": self.bell, "who": "nope"}, {"thread": "nope", "who": mira},
            {"thread": self.bell, "who": self.bell}, {"thread": self.bell, "who": mira, "role": 5},
            {"who": mira}, 5,
        ):  # fmt: skip
            with self.assertRaises(RecapError) as bad:
                await plot_threads.queue_involve(self.web, self.session, self.cref, body)
            self.assertEqual(bad.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_part_of_sixty_characters_goes_through_the_table_check_too(self) -> None:
        mira = await self.add("Mira", "character")
        part = ("quest giver " * 5)[:60].strip()
        await self.involve(self.bell, mira, part + "   ")  # (spaces count after collapsing)
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertEqual(await self.involved(self.bell), [(mira, part)])
        with self.assertRaises(RecapError):
            await self.involve(self.bell, mira, "x" * 61)

    async def test_a_second_add_of_the_same_name_is_not_an_error(self) -> None:
        mira = await self.add("Mira", "character")
        await self.involve(self.bell, mira, "villain")
        await self.involve(self.bell, mira, "villain")
        outcomes = await self.bot_loop()
        self.assertEqual([bool(o.error) for o in outcomes], [False, False])
