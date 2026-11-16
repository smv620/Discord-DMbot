"""How a recap reads, scored (#4556): four points, fixed reasons, flags only, and none of the
recap's words in the report. Invented words."""

from __future__ import annotations

import asyncio
import json
import unittest

from dmbot.ai import Reply
from dmbot.devtools.recap_run import score as sc
from dmbot.recaps import storytelling as st


def answer(score: int = 4, reason: str = "strong", **kw: tuple[int, str]) -> str:
    return json.dumps(
        {p: {"score": kw.get(p, (score, reason))[0], "reason": kw.get(p, (score, reason))[1]}
         for p in st.POINTS}
    )  # fmt: skip


class Client:
    def __init__(self, text: str | Exception) -> None:
        self.text, self.sent, self.system = text, [], []

    async def complete(self, system: str, text: str, **kw: object) -> Reply:
        self.sent.append(text)
        self.system.append(system)
        if isinstance(self.text, Exception):
            raise self.text
        return Reply(self.text, False, 10, 10, "m")


class Parsing(unittest.TestCase):
    def test_four_points_are_read_with_their_reasons(self) -> None:
        found = st.parse(answer(3, "some"))
        assert found is not None
        self.assertEqual(set(found), set(st.POINTS))
        self.assertEqual(st.average(found), 3.0)

    def test_a_point_out_of_range_or_with_an_unknown_reason_is_left_out(self) -> None:
        raw = json.dumps(
            {
                "scene": {"score": 9, "reason": "strong"},
                "characters": {"score": 3, "reason": "Mira was brave"},
                "momentum": {"score": 2, "reason": "list"},
            }
        )
        found = st.parse(raw)
        assert found is not None
        self.assertEqual(list(found), ["momentum"])

    def test_nonsense_is_unreadable_and_nothing_is_guessed(self) -> None:
        self.assertIsNone(st.parse("not json"))
        self.assertIsNone(st.parse("[1]"))
        self.assertIsNone(st.average({}))

    def test_a_fenced_answer_is_read(self) -> None:
        self.assertIsNotNone(st.parse("```json\n" + answer() + "\n```"))


class TheReport(unittest.TestCase):
    def test_it_shows_numbers_and_fixed_words_only(self) -> None:
        text = "\n".join(st.lines(st.parse(answer(2, "list")), examples=4))
        self.assertIn("scene and stakes: 2 (reads as a list of events)", text)
        self.assertIn("average: 2.0", text)
        self.assertIn("beside 4 recap example(s)", text)

    def test_an_unreadable_answer_and_a_missing_point_say_so(self) -> None:
        self.assertIn("could not be read", "\n".join(st.lines(None, examples=0)))
        found = st.parse(json.dumps({"scene": {"score": 5, "reason": "strong"}}))
        self.assertIn("momentum: unread", "\n".join(st.lines(found, examples=0)))

    def test_the_judge_reads_the_examples_and_the_recap_as_quoted_data(self) -> None:
        text = st.message(["An <example> tale."], ["Mira opened the lock."])
        self.assertIn('<example number="1">', text)
        self.assertNotIn("<example> tale", text)  # no bracket can close the tag
        self.assertIn("Mira opened the lock.", text)
        self.assertIn("never instructions", st.SYSTEM)
        self.assertIn(
            "the fact check", st.SYSTEM.lower().replace("facts are checked", "the fact check")
        )


class AsksOnce(unittest.TestCase):
    def test_the_scorer_prints_no_word_of_the_recap(self) -> None:
        client = Client(answer())
        lines = asyncio.run(sc.story_score("Mira opened the silver lock.", ["Ex."], client))
        self.assertEqual(len(client.sent), 1)
        self.assertNotIn("silver lock", "\n".join(lines))

    def test_a_failed_call_says_the_kind_only(self) -> None:
        lines = asyncio.run(sc.story_score("Mira wept.", [], Client(RuntimeError("Mira wept"))))
        text = "\n".join(lines)
        self.assertIn("the judge failed (RuntimeError)", text)
        self.assertNotIn("Mira", text)

    def test_the_main_score_adds_it_only_when_examples_are_given(self) -> None:
        for examples, calls in ((None, 1), ([], 2), (["Ex."], 2)):
            client = Client(json.dumps({"items": [{"n": 1, "state": "covered"}]}))
            asyncio.run(sc.score("Mira wept.", None, ["A."], None, client, examples=examples))
            self.assertEqual(len(client.sent), calls)
