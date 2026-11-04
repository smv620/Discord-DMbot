"""`recap_run --score` (#3328): the counts it prints (covered, missed, not given, unread; the fact
check's passes) and nothing of the recap's words; a failed or unreadable check says so plainly;
the option goes with --session or --transcript only. Invented words."""

from __future__ import annotations

import asyncio
import json
import unittest

from dmbot.ai import Reply
from dmbot.devtools.recap_run import __main__ as rr
from dmbot.devtools.recap_run import score as sc
from dmbot.recaps import must_haves

CHECKS = [("fact check", True, ""), ("ending", False, "x"), ("names", True, "")]


class Client:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.sent: list[str] = []

    async def complete(self, system: str, text: str, **kw: object) -> Reply:
        self.sent.append(text)
        if isinstance(self.answer, Exception):
            raise self.answer
        return Reply(self.answer, False, 10, 10, "m")


def says(*states: str) -> str:
    return json.dumps({"items": [{"n": n, "state": s} for n, s in enumerate(states, 1)]})


class Scoring(unittest.TestCase):
    def test_the_report_has_counts_and_one_state_a_line(self) -> None:
        lines = sc.format_score({1: "covered", 2: "missed", 3: "not_given"}, 3, CHECKS)
        text = "\n".join(lines)
        self.assertIn("covered 1, missed 1, not given 1, unread 0", text)
        self.assertIn("   2  missed", text)
        self.assertIn("2 of 3 passed", text)

    def test_an_unreadable_check_is_said_not_scored(self) -> None:
        self.assertIn("could not be read", "\n".join(sc.format_score(None, 3, CHECKS)))

    def test_with_no_list_of_its_own_it_scores_the_defaults(self) -> None:
        self.assertEqual(sc.must_list([]), must_haves.default_texts())
        self.assertEqual(sc.must_list(["Name the loot."]), ["Name the loot."])

    def test_it_asks_once_and_prints_no_word_of_the_recap(self) -> None:
        client = Client(says("covered", "missed"))
        lines = asyncio.run(
            sc.score("Mira opened the silver lock. Oskar wept.", None, ["A.", "B."], CHECKS, client)
        )
        self.assertEqual(len(client.sent), 1)
        self.assertIn("<must_haves>", client.sent[0])
        self.assertNotIn("silver lock", "\n".join(lines))

    def test_a_failed_call_says_the_kind_only(self) -> None:
        lines = asyncio.run(
            sc.score("Mira wept.", None, ["A."], CHECKS, Client(RuntimeError("secret words")))
        )
        text = "\n".join(lines)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret words", text)


class TheOption(unittest.TestCase):
    def test_score_goes_with_a_session_or_a_transcript_only(self) -> None:
        for argv in (["--golden", "--score"], ["--score"], ["--review", "x", "--score"]):
            with self.assertRaises(SystemExit, msg=str(argv)):
                rr.parse_args(argv)


class FromAFile(unittest.TestCase):
    """`--score-recap FILE --beats FILE` (#3328): an earlier recap scored against the same
    session's saved beat sheet; no fact check; nothing of the recap printed."""

    BEATS = json.dumps(
        [
            {
                "text": "Mira opened the silver lock.",
                "who": ["Mira"],
                "where": "the keep",
                "line": 1,
                "kind": "event",
            }
        ]
    )

    def test_the_cue_free_section_of_a_saved_recap_is_the_recap(self) -> None:
        from dmbot.devtools.recap_run import review as rv

        text = rv.recap_text("T", "《Tense》 Mira opened the lock.", ["h"], ["m"], voice_tags=False)
        self.assertEqual(sc.recap_from_file(text), "Mira opened the lock.")

    def test_a_plain_file_is_the_recap_as_it_is(self) -> None:
        self.assertEqual(sc.recap_from_file("  Mira wept.\n"), "Mira wept.")

    def test_a_beats_file_must_be_a_list_of_objects(self) -> None:
        self.assertIsNotNone(sc.beats_from_file(self.BEATS))
        for bad in ("not json", "{}", "[1]", ""):
            self.assertIsNone(sc.beats_from_file(bad), msg=bad)

    def test_it_scores_without_a_fact_check_and_prints_no_word_of_the_recap(self) -> None:
        client = Client(says("covered", "missed", "not_given"))
        lines = asyncio.run(
            sc.score_recap_file(
                "Mira opened the silver lock.", self.BEATS, ["A.", "B.", "C."], client
            )
        )
        text = "\n".join(lines)
        self.assertIn("covered 1, missed 1, not given 1", text)
        self.assertNotIn("Fact check", text)
        self.assertNotIn("silver lock", text)
        self.assertEqual(len(client.sent), 1)

    def test_a_file_that_is_not_a_beat_sheet_makes_no_call(self) -> None:
        client = Client(says("covered"))
        lines = asyncio.run(sc.score_recap_file("Mira wept.", "not json", ["A."], client))
        self.assertIn("not a saved beat sheet", "\n".join(lines))
        self.assertEqual(client.sent, [])

    def test_an_empty_recap_makes_no_call(self) -> None:
        client = Client(says("covered"))
        lines = asyncio.run(sc.score_recap_file("   ", self.BEATS, ["A."], client))
        self.assertIn("no recap", "\n".join(lines))
        self.assertEqual(client.sent, [])

    def test_the_options_go_together(self) -> None:
        for argv in (["--score-recap", "a.txt"], ["--session", "1", "c", "2", "--beats", "b.json"]):
            with self.assertRaises(SystemExit, msg=str(argv)):
                rr.parse_args(argv)
        got = rr.parse_args(["--score-recap", "a.txt", "--beats", "b.json"])
        self.assertEqual((got.score_recap, got.beats), ("a.txt", "b.json"))
