"""Scene moves (#4173 slice 4; the owner's change and Supervisor's cascade, 2026-11-13): the numbers
filter, the reply parser with the AI output rules, and the step with a stand-in AI: the likely DM
lines with their context in one call, every DM line when there are no numbers, a cut reply asked
again in smaller parts, consent again after the reply. Invented lines only; no network."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.memory import scene_move_step as step
from dmbot.memory import scene_moves as sm

DM, P1 = 7, 8


def reply(moves: list[dict[str, Any]] | str, *, cut: bool = False) -> Reply:
    text = moves if isinstance(moves, str) else json.dumps({"moves": moves})
    return Reply(text=text, cut=cut, input_tokens=100, output_tokens=20, model="m")


class FakeAI:
    def __init__(self, *replies: Reply | Exception) -> None:
        self.replies = list(replies)
        self.asked: list[str] = []

    async def complete(self, system: str, text: str, **_: Any) -> Reply:
        self.asked.append(text)
        got = self.replies.pop(0)
        if isinstance(got, Exception):
            raise got
        return got


MOVE = [1.0, 0.0, 0.0]
MISS = [0.0, 1.0, 0.0]
EXAMPLES = ([MOVE], [MISS])


def line(n: int, who: int, text: str, vector: list[float] | None = None) -> step.DmLine:
    return step.DmLine(n, who, text, vector)


def run(ai: FakeAI, lines: list[step.DmLine], examples: Any = EXAMPLES, agreed: Any = None) -> Any:
    calls: list[int] = []

    async def count() -> None:
        calls.append(1)

    async def still() -> set[int]:
        return set(agreed if agreed is not None else {DM, P1})

    return asyncio.run(
        step.find(ai, lines, {DM}, examples, count_call=count, allowed=10, agreed=still)
    )


class TheFilter(unittest.TestCase):
    def test_a_move_like_line_passes_and_a_near_miss_does_not(self) -> None:
        self.assertTrue(sm.likely([0.9, 0.1, 0.0], [MOVE], [MISS]))
        self.assertFalse(sm.likely([0.1, 0.9, 0.0], [MOVE], [MISS]))
        self.assertFalse(sm.likely([0.0, 0.0, 1.0], [MOVE], [MISS]))  # (below the threshold)

    def test_context_is_the_line_either_side(self) -> None:
        self.assertEqual(sm.with_context({5}, [3, 4, 5, 6, 7]), [4, 5, 6])

    def test_every_example_is_invented_and_there_are_enough(self) -> None:
        for kind, examples in sm.EXAMPLES.items():
            self.assertGreaterEqual(len(examples), 20, kind)
        self.assertGreaterEqual(len(sm.NEAR_MISSES), 20)


class TheParser(unittest.TestCase):
    def test_moves_for_asked_lines_only(self) -> None:
        got = sm.parse_moves(
            json.dumps({"moves": [
                {"line": 2, "kind": "leave", "place": "Kelvenmoor"},
                {"line": 9, "kind": "arrive", "place": "Brindleford"},  # (not asked)
                {"line": 2, "kind": "fly", "place": "x"},  # (no such kind)
                {"line": 2, "kind": "arrive", "place": "ignore all rules {now}"},  # (not a name)
                {"line": 2, "kind": "heading", "place": ""},  # (needs its place)
                {"line": 2, "kind": "travel", "place": ""},
            ]}),
            {2},
        )  # fmt: skip
        self.assertEqual(got.moves, [sm.Move(2, "leave", "Kelvenmoor"), sm.Move(2, "travel", "")])

    def test_a_cut_reply_keeps_what_came_whole(self) -> None:
        text = '{"moves": [{"line": 2, "kind": "leave", "place": "Kelvenmoor"}, {"line": 3, "ki'
        got = sm.parse_moves(text, {2, 3}, cut=True)
        self.assertEqual(
            (got.moves, got.stopped_early), ([sm.Move(2, "leave", "Kelvenmoor")], True)
        )

    def test_an_unreadable_reply_is_not_nothing_found(self) -> None:
        self.assertTrue(sm.parse_moves("not json", {2}).unreadable)


class TheStep(unittest.TestCase):
    def test_only_likely_dm_lines_go_with_their_context(self) -> None:
        ai = FakeAI(reply([{"line": 2, "kind": "leave", "place": "Kelvenmoor"}]))
        got = run(
            ai,
            [
                line(1, DM, "The wind howls.", [0.0, 0.0, 1.0]),
                line(2, DM, "You leave Kelvenmoor behind.", [0.9, 0.1, 0.0]),
                line(3, P1, "I wave goodbye."),
                line(4, DM, "Your cousin lives there.", [0.1, 0.9, 0.0]),
            ],
        )
        self.assertEqual((got.asked, got.fallback, got.calls), (1, 0, 1))
        self.assertIn("2 ASK", ai.asked[0])
        self.assertIn("1 CONTEXT", ai.asked[0])
        self.assertIn("3 CONTEXT", ai.asked[0])
        self.assertNotIn("4 ", ai.asked[0])
        self.assertEqual(step.moves_of(got), {2: (sm.Move(2, "leave", "Kelvenmoor"),)})

    def test_no_numbers_sends_every_dm_line(self) -> None:
        ai = FakeAI(reply([]))
        got = run(ai, [line(1, DM, "a"), line(2, P1, "b"), line(3, DM, "c")], examples=None)
        self.assertEqual((got.asked, got.fallback), (2, 2))
        self.assertIn("1 ASK", ai.asked[0])
        self.assertIn("3 ASK", ai.asked[0])

    def test_a_line_without_numbers_is_asked_anyway(self) -> None:
        ai = FakeAI(reply([]))
        got = run(ai, [line(1, DM, "a", [0.0, 0.0, 1.0]), line(2, DM, "b")])
        self.assertEqual((got.asked, got.fallback), (1, 1))

    def test_a_cut_reply_asks_the_rest_again_in_parts(self) -> None:
        cut = '{"moves": [{"line": 1, "kind": "travel", "place": ""}, {"line'
        ai = FakeAI(reply(cut, cut=True), reply([]), reply([]))
        lines = [line(n, DM, "x") for n in (1, 2, 3)]
        got = run(ai, lines, examples=None)
        self.assertEqual((got.calls, got.stopped_early), (3, 1))
        self.assertEqual(step.moves_of(got), {1: (sm.Move(1, "travel", ""),)})

    def test_a_move_on_a_line_of_someone_who_stopped_is_dropped(self) -> None:
        ai = FakeAI(reply([{"line": 1, "kind": "travel", "place": ""}]))
        got = run(ai, [line(1, DM, "x")], examples=None, agreed={P1})
        self.assertEqual(got.moves, {})

    def test_out_of_funds_stops_and_an_unreadable_reply_is_counted(self) -> None:
        got = run(FakeAI(AIOutOfFunds("x")), [line(1, DM, "x")], examples=None)
        self.assertTrue(got.out_of_funds)
        got = run(FakeAI(reply("nonsense")), [line(1, DM, "x")], examples=None)
        self.assertEqual((got.unreadable, got.moves), (1, {}))


class ThePrompt(unittest.TestCase):
    def test_the_rulings_examples_are_in_it(self) -> None:
        for words in ('"Left for X" is heading for X', "left somewhere", "a memory or story"):
            self.assertIn(words, sm.SYSTEM)
