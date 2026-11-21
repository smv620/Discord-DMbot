"""The website's Plot threads panel (#3696 slice 1, migration 0576): only a DM reads it, it gives
each thread's state in core's words, who is involved, the history in core's lines, and the
stalled flag
worked out by `threads.is_stalled` from the count function (never stored). The pure shaping is
tested on handmade rows (a stalled thread, a promise, led-to links, the order); the door on a real
campaign. Postgres. Invented names."""

from __future__ import annotations

import time
import unittest
from typing import Any

from dmbot.memory import threads
from dmbot.memory.models import GOING, OPEN, RESOLVED
from dmbot.web import plot_threads, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_relationships import Web

T1, T2, T3, P1, A, B = "t1" * 16, "t2" * 16, "t3" * 16, "p1" * 16, "aa" * 16, "bb" * 16


def rows(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "total": 4,
        "threads": [
            {"id": T1, "name": "The drowned bell", "type": "quest", "description": ""},
            {"id": T2, "name": "Old grudge", "type": "thread", "description": ""},
            {"id": T3, "name": "A finished tale", "type": "thread", "description": ""},
            {
                "id": P1,
                "name": "Bring the healer back",
                "type": "promise",
                "description": "by Samhain",
            },
        ],
        "states": [
            {"entity": T1, "condition": OPEN, "fromSession": 100, "createdAt": 5, "closedAt": 20},
            {
                "entity": T1,
                "condition": GOING,
                "fromSession": 200,
                "createdAt": 20,
                "closedAt": None,
            },
            {
                "entity": T2,
                "condition": OPEN,
                "fromSession": None,
                "createdAt": 5,
                "closedAt": None,
            },
            {
                "entity": T3,
                "condition": RESOLVED,
                "fromSession": 100,
                "createdAt": 5,
                "closedAt": None,
            },
            {
                "entity": P1,
                "condition": OPEN,
                "fromSession": None,
                "createdAt": 5,
                "closedAt": None,
            },
        ],
        "facts": [
            {
                "subject": A,
                "predicate": "in_thread",
                "object": T1,
                "detail": "quest giver",
                "fromSession": None,
            },
            {"subject": B, "predicate": "advances", "object": T1, "detail": "", "fromSession": 200},
            {
                "subject": A,
                "predicate": "promised_by",
                "object": P1,
                "detail": "",
                "fromSession": None,
            },
            {
                "subject": B,
                "predicate": "promised_to",
                "object": P1,
                "detail": "",
                "fromSession": None,
            },
            {"subject": T2, "predicate": "led_to", "object": T1, "detail": "", "fromSession": None},
            {
                "subject": "gone",
                "predicate": "in_thread",
                "object": T1,
                "detail": "x",
                "fromSession": None,
            },
        ],
        "names": {A: "Mira", B: "The Toll", T2: "Old grudge", T1: "The drowned bell"},
        "quiet": {T1: [300, 0], T2: [50, 4], T3: [50, 9], P1: [50, 5]},
        "numbers": {"100": 1, "200": 2},
    }
    base.update(over)
    return base


class TheShaping(unittest.TestCase):
    def test_a_thread_says_its_state_people_movers_and_history_in_cores_words(self) -> None:
        got = plot_threads.shape(rows(), 3)
        bell = next(t for t in got["threads"] if t["id"] == T1)
        self.assertEqual((bell["state"], bell["stateWords"]), (GOING, "Happening now"))
        self.assertEqual(bell["history"], ["Not started (Session 1)", "Happening now (Session 2)"])
        self.assertEqual(bell["involved"], [{"id": A, "name": "Mira", "role": "quest giver"}])
        self.assertEqual(
            bell["movers"], [{"id": B, "name": "The Toll", "endsIt": False, "session": 2}]
        )
        self.assertEqual(bell["cameFrom"], [{"id": T2, "name": "Old grudge"}])
        self.assertFalse(bell["stalled"])
        self.assertIsNone(bell["promise"])

    def test_a_fact_from_an_unconfirmed_name_is_left_out(self) -> None:
        got = plot_threads.shape(rows(), 3)
        bell = next(t for t in got["threads"] if t["id"] == T1)
        self.assertEqual([p["name"] for p in bell["involved"]], ["Mira"])

    def test_stalled_is_worked_out_from_the_count_and_the_after_setting_never_stored(self) -> None:
        got = plot_threads.shape(rows(), 3)
        by = {t["id"]: t for t in got["threads"]}
        self.assertTrue(by[T2]["stalled"])  # open, quiet for 4
        self.assertFalse(by[T3]["stalled"])  # finished: never stalled
        self.assertTrue(by[P1]["stalled"])
        self.assertEqual(got["stalledCount"], 2)
        self.assertEqual(
            got["stalledLine"], threads.stalled_line(2, 3, promises=1)
        )  # (core's own line, a promise counted in)
        again = plot_threads.shape(rows(), 6)  # a longer wait: nothing is stalled
        self.assertEqual((again["stalledCount"], again["stalledLine"]), (0, None))

    def test_a_promise_says_who_and_to_whom_and_the_due_note_as_given(self) -> None:
        got = plot_threads.shape(rows(), 3)
        promise = next(t for t in got["threads"] if t["id"] == P1)["promise"]
        self.assertEqual(promise, {"by": ["Mira"], "to": ["The Toll"], "due": "by Samhain"})
        self.assertEqual(
            next(t for t in got["threads"] if t["id"] == P1)["stateWords"], "Open"
        )  # (a promise's own words)

    def test_the_order_is_happening_now_then_open_by_quietest_then_the_rest(self) -> None:
        got = plot_threads.shape(rows(), 3)
        self.assertEqual(
            [t["id"] for t in got["threads"]], [T1, P1, T2, T3]
        )  # going; open (quiet 5, then 4); resolved last

    def test_a_thread_with_no_state_has_no_words(self) -> None:
        got = plot_threads.shape(rows(states=[]), 3)
        self.assertTrue(all(t["stateWords"] is None and t["history"] == [] for t in got["threads"]))


class TheDoor(Web):
    bob_has_a_hero = False

    async def test_a_dm_reads_a_thread_with_its_state_and_people(self) -> None:
        bell = await self.add("The drowned bell", "quest")
        await self.memory.set_condition(
            self.g, self.one.id, bell, GOING, source="dm", from_session_at=int(time.time()) - 50
        )
        await self.memory.involve(self.g, self.one.id, bell, self.mira, "quest giver", source="dm")
        got = await plot_threads.read(self.web, self.session, self.cref)
        mine = next(t for t in got["threads"] if t["id"] == bell)
        self.assertEqual((mine["kind"], mine["stateWords"]), ("quest", "Happening now"))
        self.assertEqual(
            [(p["name"], p["role"]) for p in mine["involved"]], [("Mira Voss", "quest giver")]
        )
        self.assertEqual(got["after"], threads.STALLED_AFTER)
        self.assertEqual(got["total"], 1)

    async def test_a_player_is_refused(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await plot_threads.read(self.web, bob, self.cref)
        self.assertEqual(err.exception.code, "not_allowed")


class TheKinds(unittest.TestCase):
    def test_the_door_lists_the_kinds_the_seed_ontology_gives_a_thread(self) -> None:
        from pathlib import Path

        from dmbot.memory.models import THREAD_TYPES

        text = (
            Path(plot_threads.__file__)
            .parents[1]
            .joinpath("migrations", "0576_web_plot_threads.py")
            .read_text()
        )
        for kind in THREAD_TYPES:
            self.assertIn(f"'{kind}'", text, kind)
        self.assertEqual(text.count("ARRAY['thread'"), 1)


class TheEnvironment(unittest.TestCase):
    def test_the_stalled_after_setting_reads_like_the_bots(self) -> None:
        import os
        from unittest import mock

        for value, expected in (
            ("", threads.STALLED_AFTER), ("5", 5), ("0", threads.STALLED_AFTER),
            ("abc", threads.STALLED_AFTER), ("\u00b2", threads.STALLED_AFTER),
        ):  # fmt: skip
            with mock.patch.dict(os.environ, {"DMBOT_THREAD_STALLED_SESSIONS": value}):
                self.assertEqual(plot_threads.stalled_after(), expected, value)

    def test_the_play_order_has_four_ranks_and_ties_go_by_name_then_id(self) -> None:
        key = threads.play_key
        self.assertLess(key(GOING, 0, "z", "1"), key(OPEN, 9, "a", "1"))
        self.assertLess(key(OPEN, 0, "z", "1"), key("hinted", 9, "a", "1"))
        self.assertLess(key("hinted", 0, "z", "1"), key(RESOLVED, 9, "a", "1"))
        self.assertLess(key(OPEN, 5, "z", "1"), key(OPEN, 2, "a", "1"))  # the quietest first
        self.assertLess(key(OPEN, 2, "a", "2"), key(OPEN, 2, "B", "1"))  # name, any case
        self.assertLess(key(OPEN, 2, "a", "1"), key(OPEN, 2, "a", "2"))  # then id
        self.assertEqual(key(None, 4, "x", "1")[0], 3)  # no state: after the live ones


class TheCap(Web):
    bob_has_a_hero = False

    async def test_more_than_a_hundred_list_a_hundred_and_say_how_many_there_are(self) -> None:
        for n in range(103):
            await self.add(f"Thread {n:03d}", "thread")
        got = await plot_threads.read(self.web, self.session, self.cref)
        self.assertEqual((len(got["threads"]), got["total"]), (100, 103))

    async def test_a_suggested_thread_is_not_listed(self) -> None:
        guess = await self.add("A guess", "thread")
        await self.sql("UPDATE memory_entities SET status = 'proposed' WHERE id = %s", guess)
        got = await plot_threads.read(self.web, self.session, self.cref)
        self.assertNotIn("A guess", [t["name"] for t in got["threads"]])
