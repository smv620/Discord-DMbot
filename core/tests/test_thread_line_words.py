"""The website's words for a suggestion's lines are Discord's (#4501): `web/thread_line_words.py`
cannot import the Discord card (an import contract), so every kind, state, due, limit and name case
is held equal to `ui/thread_questions.line_for` here. No database."""

from __future__ import annotations

import itertools
import unittest

from dmbot.memory import thread_parts as tp
from dmbot.memory.models import HINTED
from dmbot.memory.thread_read import ReadBranch, ReadPart
from dmbot.ui.thread_questions import line_for
from dmbot.web.thread_line_words import line_words

NAMES = {"w": "Hlin Trollbane", "b0": "Wit"}


class SameWords(unittest.TestCase):
    def test_every_part_reads_as_the_discord_card_reads_it(self) -> None:
        cases = itertools.product(
            (tp.OBJECTIVE, tp.COMPLICATION, tp.REWARD, tp.REWARD_GIVEN, tp.HELP, tp.TIME_LIMIT),
            ("to_do", "owed", "to_keep", "lent", "running"),
            ("", tp.UP_FRONT, tp.AFTERWARDS),
            ("", tp.HARD, tp.SOFT),
            ("", "Back by the thaw", "x" * 200),
            (None, "w"),
            (frozenset[str](), frozenset({"w"})),
        )
        for kind, state, due, limit, terms, who, waiting in cases:
            part = ReadPart(kind, "Find the heir " * 10, state, "q", who, due, terms, limit)
            if kind == tp.HELP and state not in ("to_keep", "lent"):
                continue  # (a help is kept or lent: no other state reaches a line)
            if due and kind != tp.REWARD:
                continue  # (a date for the reward's giving is a reward's only)
            self.assertEqual(
                line_words(part, NAMES, waiting), line_for(part, NAMES, waiting), (part, waiting)
            )

    def test_every_branch_reads_as_the_discord_card_reads_it(self) -> None:
        for state, people, waiting in itertools.product(
            (HINTED, "to_do"),
            ((), (("the one missing", "b0"), ("target", "gone"))),
            (frozenset[str](), frozenset({"b0"})),
        ):
            branch = ReadBranch("mystery", "Who took her? " * 10, "aim", "q", state, people)
            self.assertEqual(
                line_words(branch, NAMES, waiting), line_for(branch, NAMES, waiting), (branch,)
            )
