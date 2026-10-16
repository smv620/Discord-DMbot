"""The recap measurement (#1382): the golden set's format, the scoring on canned answers, and
that nothing costs money unasked. No test here calls the AI."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.devtools.recap_measure import __main__ as measure
from dmbot.devtools.recap_measure.golden import GoldenError, load_golden, parse
from dmbot.recaps import draft as rd
from dmbot.recaps.text import names_a_secret

SESSIONS = load_golden()


def perfect(session: Any) -> list[dict[str, Any]]:
    """A sheet that cites, for each reference beat, the lines that hold its key words."""
    out: list[dict[str, Any]] = []
    for keys in session.reference:
        cited = [
            n
            for n, line in enumerate(session.lines, start=1)
            if sum(k[:5] in line.text.lower() for k in keys) * 3 >= len(keys) * 2
        ]
        if cited:
            text = " ".join(session.lines[cited[0] - 1].text.split()[:12])
            out.append({"what": text, "who": [], "lines": cited[:2], "kind": "event"})
    return out


class TheSet(unittest.TestCase):
    def test_every_session_loads_and_has_beats_a_secret_and_enough_lines(self) -> None:
        self.assertGreaterEqual(len(SESSIONS), 3)
        for s in SESSIONS:
            self.assertGreaterEqual(len(s.reference), 5, s.name)
            self.assertTrue(s.secret, s.name)

    def test_one_session_is_long_enough_to_need_three_windows(self) -> None:
        from dmbot.recaps import beats as bt

        long = [s for s in SESSIONS if s.window_chars < 40_000]
        self.assertTrue(long)
        for s in long:
            rows = [(str(n), "x", line.text) for n, line in enumerate(s.lines, start=1)]
            self.assertGreaterEqual(len(bt.windows(rows, s.window_chars)), 3, s.name)

    def test_a_malformed_file_is_refused_with_its_name(self) -> None:
        good = json.loads(json.dumps({
            "title": "t", "names": [], "secret": ["X"],
            "lines": [["dm", f"line {n}"] for n in range(10)],
            "beats": [{"keys": ["a"]}] * 3,
        }))  # fmt: skip
        parse("ok", good)
        changes: list[dict[str, Any]] = [
            {"lines": []},
            {"lines": [["zed", "x"]] * 10},
            {"lines": [["dm"]] * 10},
            {"beats": [{"keys": ["a"]}]},
            {"secret": []},
            {"window_chars": 5},
            {"window_chars": "big"},
            {"title": None, "beats": None},
        ]
        for change in changes:
            with self.assertRaises(GoldenError, msg=str(change)):
                parse("bad.json", {**good, **change})
        with self.assertRaises(GoldenError):
            parse("x", [])

    def test_a_secret_name_is_never_said_in_its_own_session(self) -> None:
        for s in SESSIONS:
            said = " ".join(line.text for line in s.lines)
            self.assertFalse(names_a_secret(said, s.secret, []), s.name)


class TheScoring(unittest.TestCase):
    def test_a_good_sheet_finds_the_beats_and_invents_none(self) -> None:
        for s in SESSIONS:
            one = measure.score_session(s, perfect(s), None, s.title)
            self.assertEqual(one.invented, 0, s.name)
            self.assertGreaterEqual(one.recall, 0.8, s.name)

    def test_an_invented_beat_is_counted(self) -> None:
        s = SESSIONS[0]
        raw = [*perfect(s), {"what": "A dragon came", "who": [], "lines": [99], "kind": "event"}]
        self.assertEqual(measure.score_session(s, raw, None, s.title).invented, 1)

    def test_a_missing_beat_lowers_recall(self) -> None:
        s = SESSIONS[0]
        full = measure.score_session(s, perfect(s), None, s.title)
        fewer = measure.score_session(s, perfect(s)[:2], None, s.title)
        self.assertLess(fewer.recall, full.recall)

    def test_quote_and_secret_leaks_are_counted(self) -> None:
        s = SESSIONS[0]
        copied = " ".join(s.lines[2].text.split() * 1)
        prose = rd.Prose(f"《Tense》 {copied} and then {s.secret[0]} arrived.", ())
        one = measure.score_session(s, perfect(s), prose, s.title)
        self.assertEqual((one.quote_leaks, one.secret_leaks), (1, 1))
        clean = rd.Prose("《Tense》 The party crossed in silence.", ())
        again = measure.score_session(s, perfect(s), clean, s.title)
        self.assertEqual((again.quote_leaks, again.secret_leaks), (0, 0))

    def test_the_report_lists_every_number(self) -> None:
        total = measure.Score()
        measure.add(total, measure.score_session(SESSIONS[0], perfect(SESSIONS[0]), None, "t"))
        text = "\n".join(measure.report(total))
        for word in ("beat recall", "invented beats", "quote leaks", "secret leaks"):
            self.assertIn(word, text)


class NothingCostsMoneyUnasked(unittest.TestCase):
    def test_the_default_makes_no_call_and_a_paid_run_needs_a_key(self) -> None:
        self.assertEqual(measure.main([]), 0)
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertEqual(measure.main(["--writer", "anthropic"]), 2)


if __name__ == "__main__":
    unittest.main()
