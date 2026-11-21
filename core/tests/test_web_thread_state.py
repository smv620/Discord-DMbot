"""A plot thread's state from the website (#3696 slice 2a, migration 0580): only a DM queues it, the
bot runs the card's own store calls (`set_condition`, `check_thread`), only a confirmed thread of
the campaign can be moved (a character's id must never reach `set_condition`), and a thread that
cannot be checked is `gone`. Postgres. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import threads
from dmbot.memory import web_actions as wa
from dmbot.memory.models import OPEN, RESOLVED
from dmbot.web import plot_threads, relationships, sessions
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
                self.g, action, error=outcome.error, result_id=outcome.result_id,
                result_counts=outcome.counts, result_batches=outcome.batches,
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


class StartingAThreadOrAPromise(ThreadWeb):
    """Slice 2c (migration 0582): a new thread or promise, a promise's due note, its parties."""

    async def new(self, kind: str, name: str, state: str = "open") -> str:
        return await plot_threads.queue_new(
            self.web, self.session, self.cref, {"kind": kind, "state": state, "name": name}
        )

    async def made(self, name: str) -> Any:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT id, type, status, description FROM memory_entities"
                " WHERE campaign_id = %s AND name = %s", (self.one.id, name),
            )  # fmt: skip
            return await cur.fetchone()

    async def promise(self, name: str = "Bring the healer back") -> str:
        made = await self.memory.new_thread(self.g, self.one.id, name, "promise", OPEN, source="dm")
        return str(made.value.id)

    async def test_every_kind_can_be_started_and_the_answer_carries_its_id(self) -> None:
        for kind in ("thread", "quest", "mystery", "hook", "goal", "promise"):
            answer = await self.new(kind, f"A new {kind}")
            (outcome,) = await self.bot_loop()
            self.assertFalse(outcome.error)
            row = await self.made(f"A new {kind}")
            self.assertEqual((row["type"], row["status"]), (kind, "confirmed"))
            self.assertEqual(outcome.result_id, row["id"])
            got = await relationships.read_answer(self.web, self.session, self.cref, answer)
            self.assertEqual((got["state"], got["id"]), ("done", row["id"]))

    async def test_a_name_of_a_hundred_characters_goes_through_the_table_check(self) -> None:
        name = "The long road to the " + "far " * 19
        name = name.strip()[:100]
        await self.new("quest", name)
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertIsNotNone(await self.made(name))

    async def test_a_due_note_is_saved_and_an_empty_one_clears_it(self) -> None:
        promise = await self.promise()
        due = "By the full moon " + "x" * 100
        for text in (due[:120], ""):
            await plot_threads.queue_due(
                self.web, self.session, self.cref, {"promise": promise, "due": text}
            )
            (outcome,) = await self.bot_loop()
            self.assertFalse(outcome.error)
            row = await self.made("Bring the healer back")
            self.assertEqual(row["description"], text)

    async def test_a_due_note_for_a_quest_or_a_made_up_id_is_gone(self) -> None:
        for target in (self.bell, "a" * 32):
            await plot_threads.queue_due(
                self.web, self.session, self.cref, {"promise": target, "due": "soon"}
            )
        outcomes = await self.bot_loop()
        self.assertEqual([o.error for o in outcomes], [wa.GONE, wa.GONE])

    async def test_who_promised_and_who_it_was_promised_to(self) -> None:
        promise = await self.promise()
        mira = await self.add("Mira", "character")
        keep = await self.add("Greywater Keep", "place")
        for side, who in (("by", mira), ("to", keep)):
            await plot_threads.queue_party(
                self.web, self.session, self.cref, {"promise": promise, "side": side, "who": who}
            )
        outcomes = await self.bot_loop()
        self.assertEqual([bool(o.error) for o in outcomes], [False, False])
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT predicate, subject_id FROM memory_relations WHERE object_id = %s"
                " ORDER BY predicate", (promise,),
            )  # fmt: skip
            rows = [(str(r["predicate"]), str(r["subject_id"])) for r in await cur.fetchall()]
        self.assertEqual(rows, [("promised_by", mira), ("promised_to", keep)])

    async def test_a_place_cannot_make_a_promise_and_a_quest_has_no_parties(self) -> None:
        promise = await self.promise()
        keep = await self.add("Greywater Keep", "place")
        mira = await self.add("Mira", "character")
        for target, side, who in ((promise, "by", keep), (self.bell, "by", mira)):
            await plot_threads.queue_party(
                self.web, self.session, self.cref, {"promise": target, "side": side, "who": who}
            )
        first, second = await self.bot_loop()
        self.assertEqual(first.error, wa.FAILED)  # (the store refuses a place as the maker)
        self.assertEqual(second.error, wa.GONE)  # (a quest is not a promise)

    async def test_another_campaigns_promise_is_gone(self) -> None:
        theirs = await self.memory.new_thread(
            self.g, self.two.id, "Their promise", "promise", OPEN, source="dm"
        )
        await plot_threads.queue_due(
            self.web, self.session, self.cref, {"promise": theirs.value.id, "due": "soon"}
        )
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_only_a_dm_queues_these_and_bad_bodies_are_refused(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await plot_threads.queue_new(
                bob_web := self.web, bob, self.cref, {"kind": "quest", "state": "open", "name": "X"}
            )
        self.assertEqual(err.exception.code, "not_allowed")
        self.assertIs(bob_web, self.web)
        promise = await self.promise()
        bad = (
            (plot_threads.queue_new, {"kind": "person", "state": "open", "name": "X"}),
            (plot_threads.queue_new, {"kind": "quest", "state": "going", "name": "X"}),
            (plot_threads.queue_new, {"kind": "quest", "state": "open", "name": "  "}),
            (plot_threads.queue_new, {"kind": "quest", "state": "open", "name": "x" * 101}),
            (plot_threads.queue_new, 5),
            (plot_threads.queue_due, {"promise": promise, "due": "x" * 121}),
            (plot_threads.queue_due, {"promise": "nope", "due": "soon"}),
            (plot_threads.queue_party, {"promise": promise, "side": "from", "who": promise}),
            (plot_threads.queue_party, {"promise": promise, "side": "by", "who": "nope"}),
        )
        for fn, body in bad:
            with self.assertRaises(RecapError) as refused:
                await fn(self.web, self.session, self.cref, body)
            self.assertEqual(refused.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    def test_the_doors_kinds_and_states_are_the_ones_the_store_knows(self) -> None:
        import importlib

        from dmbot.memory.models import THREAD_TYPES

        mig = importlib.import_module("dmbot.migrations.0582_web_thread_new")
        self.assertEqual(mig._KINDS.split("|"), list(THREAD_TYPES))
        self.assertEqual(mig._STATES.split("|"), list(threads.ALL_STATES))

    async def test_a_party_who_is_unconfirmed_or_merged_is_gone(self) -> None:
        promise = await self.promise()
        guess = await self.add("A guess", "character")
        await self.sql("UPDATE memory_entities SET status = 'proposed' WHERE id = %s", guess)
        old = await self.add("Old Mira", "character")
        mira = await self.add("Mira", "character")
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            mira, old,
        )  # fmt: skip
        for who in (guess, old):
            await plot_threads.queue_party(
                self.web, self.session, self.cref, {"promise": promise, "side": "by", "who": who}
            )
        outcomes = await self.bot_loop()
        self.assertEqual([o.error for o in outcomes], [wa.GONE, wa.GONE])

    async def test_a_second_thread_with_the_same_name_is_the_stores_call(self) -> None:
        await self.new("quest", "The missing heir")
        await self.new("quest", "The missing heir")
        first, second = await self.bot_loop()
        self.assertFalse(first.error)
        # (whatever the store says about a repeat, the page gets a plain word, never a crash)
        self.assertIn(second.error, ("", wa.FAILED))


class LinkingEventsAndLeadsTo(ThreadWeb):
    """Slice 2d (migration 0583): "Link an event" and "Led to…" (`link_event`, `led_to`)."""

    async def link_event_to(self, thread: str, how: str, **which: str) -> str:
        return await plot_threads.queue_link(
            self.web, self.session, self.cref, {"thread": thread, "how": how, **which}
        )

    async def facts(self, predicate: str) -> list[tuple[str, str]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT subject_id, object_id FROM memory_relations WHERE predicate = %s"
                " AND status = 'confirmed' AND campaign_id = %s", (predicate, self.one.id),
            )  # fmt: skip
            return [(str(r["subject_id"]), str(r["object_id"])) for r in await cur.fetchall()]

    async def test_an_event_moves_a_thread_along_or_ends_it(self) -> None:
        ford = await self.add("The ambush at the ford", "event")
        await self.link_event_to(self.bell, "moved", event=ford)
        await self.link_event_to(self.bell, "ended", event=ford)
        first, second = await self.bot_loop()
        self.assertFalse(first.error or second.error)
        self.assertEqual(await self.facts("advances"), [(ford, self.bell)])
        self.assertEqual(await self.facts("resolves"), [(ford, self.bell)])
        self.assertEqual(len(first.batches), 1)

    async def test_an_event_typed_by_name_is_made_hidden_and_linked(self) -> None:
        await self.link_event_to(self.bell, "moved", newEvent="  The  bell rang ")
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT id, type, status, hidden_from_players FROM memory_entities"
                " WHERE campaign_id = %s AND name = 'The bell rang'", (self.one.id,),
            )  # fmt: skip
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["type"], row["status"], row["hidden_from_players"]),
                         ("event", "confirmed", True))  # fmt: skip
        self.assertEqual(await self.facts("advances"), [(row["id"], self.bell)])

    async def test_a_link_to_a_character_a_made_up_id_or_another_campaigns_thread_is_gone(
        self,
    ) -> None:
        mira = await self.add("Mira", "character")
        theirs = await self.memory.new_thread(
            self.g, self.two.id, "Their bell", "quest", OPEN, source="dm"
        )
        ford = await self.add("The ambush at the ford", "event")
        await self.link_event_to(self.bell, "moved", event=mira)
        await self.link_event_to(self.bell, "moved", event="a" * 32)
        await self.link_event_to(theirs.value.id, "moved", event=ford)
        await self.link_event_to(ford, "moved", event=ford)  # (an event is not a thread)
        outcomes = await self.bot_loop()
        self.assertEqual([o.error for o in outcomes], [wa.GONE] * 4)

    async def test_an_event_of_another_campaign_is_gone(self) -> None:
        theirs = await self.memory.add_entity(
            self.g, self.two.id, type="event", name="Their ambush", source="dm",
            status="confirmed",
        )  # fmt: skip
        await self.link_event_to(self.bell, "moved", event=theirs.value.id)
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_a_made_event_and_its_link_are_one_change(self) -> None:
        await self.link_event_to(self.bell, "ended", newEvent="The bell fell")
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertEqual(len(outcome.batches), 1)  # (the event and its link are one change, #4950)

    async def test_what_one_thing_led_to(self) -> None:
        clue = await self.add("A muddy footprint", "clue")
        ford = await self.add("The ambush at the ford", "event")
        await plot_threads.queue_led_to(
            self.web, self.session, self.cref, {"from": clue, "to": self.bell}
        )
        await plot_threads.queue_led_to(
            self.web, self.session, self.cref, {"from": self.bell, "to": ford}
        )
        outcomes = await self.bot_loop()
        self.assertEqual([bool(o.error) for o in outcomes], [False, False])
        self.assertEqual(
            sorted(await self.facts("led_to")), sorted([(clue, self.bell), (self.bell, ford)])
        )

    async def test_a_led_to_loop_the_wrong_kind_or_a_missing_entry_writes_nothing(self) -> None:
        mira = await self.add("Mira", "character")
        ford = await self.add("The ambush at the ford", "event")
        for pair in (
            (self.bell, ford),
            (ford, self.bell),
            (self.bell, mira),
            (self.bell, "b" * 32),
        ):
            await plot_threads.queue_led_to(
                self.web, self.session, self.cref, {"from": pair[0], "to": pair[1]}
            )
        outcomes = await self.bot_loop()
        self.assertEqual(
            [o.error for o in outcomes], ["", wa.FAILED, wa.FAILED, wa.GONE]
        )  # (the second closes a loop, the third is a character)
        self.assertEqual(await self.facts("led_to"), [(self.bell, ford)])

    async def test_a_name_of_a_hundred_characters_goes_through_the_table_check(self) -> None:
        name = ("The long night " * 8)[:99] + "x"
        self.assertEqual(len(name), 100)
        await self.link_event_to(self.bell, "moved", newEvent=name)
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)

    async def test_only_a_dm_queues_these_and_bad_bodies_are_refused(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        ford = await self.add("The ambush at the ford", "event")
        with self.assertRaises(RecapError) as err:
            await plot_threads.queue_link(
                self.web, bob, self.cref, {"thread": self.bell, "how": "moved", "event": ford}
            )
        self.assertEqual(err.exception.code, "not_allowed")
        bad_links: tuple[object, ...] = (
            {"thread": self.bell, "how": "pushed", "event": ford},
            {"thread": self.bell, "how": "moved"},
            {"thread": self.bell, "how": "moved", "event": ford, "newEvent": "X"},
            {"thread": self.bell, "how": "moved", "newEvent": "  "},
            {"thread": self.bell, "how": "moved", "newEvent": "x" * 101},
            {"thread": "nope", "how": "moved", "event": ford}, 5,
        )  # fmt: skip
        for body in bad_links:
            with self.assertRaises(RecapError) as refused:
                await plot_threads.queue_link(self.web, self.session, self.cref, body)
            self.assertEqual(refused.exception.code, "bad_action")
        for body in ({"from": ford, "to": ford}, {"from": ford}, {"from": "x", "to": ford}, 5):
            with self.assertRaises(RecapError) as refused:
                await plot_threads.queue_led_to(self.web, self.session, self.cref, body)
            self.assertEqual(refused.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))
