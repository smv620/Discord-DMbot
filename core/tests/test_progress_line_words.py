"""The website's words for a "Progress to check" line equal Discord's card (#4616): the website may
not import the Discord layer, so its copy (`web/progress_line_words.py`) is held equal here for
every kind, state and part kind. Pure words: no database."""

from __future__ import annotations

import unittest

from dmbot.memory import thread_parts as tp
from dmbot.memory import thread_progress as prog
from dmbot.memory.threads import PROMISE, STATE_WORDS
from dmbot.ui import thread_parts, thread_progress_card
from dmbot.web import progress_line_words as web

PART_ID = "p" * 32
CLUE_ID = "c" * 32


def row(kind: str, **more: object) -> dict[str, object]:
    return {"kind": kind, "partKind": "", "words": "", "state": "", "due": "", **more}


class TheWordsAreEqual(unittest.TestCase):
    def test_the_fixed_words_equal_the_cards(self) -> None:
        self.assertEqual(web.KIND_WORDS, dict(thread_parts.KIND_WORDS))
        self.assertEqual(web.STATE_SAID, dict(thread_parts.STATE_SAID))

    def test_a_new_part_in_every_kind(self) -> None:
        for kind in web.KIND_WORDS:
            for state, due in (("", ""), ("lent", ""), ("", "by dawn")):
                change = prog.Change(prog.PART, kind, "the old map", state, None, due)
                ours = web.line_text(
                    row("part", partKind=kind, words="the old map", state=state, due=due)
                )
                self.assertEqual(ours, thread_progress_card.line_text(change, {}), (kind, state))

    def test_a_part_moving_on_in_every_state(self) -> None:
        for state in web.STATE_SAID:
            change = prog.Change(prog.PART_STATE, "", "", state, PART_ID)
            ours = web.line_text(row("part_state", state=state, partWords="find Garret"))
            self.assertEqual(
                ours, thread_progress_card.line_text(change, {PART_ID: "find Garret"}), state
            )

    def test_a_thread_and_a_promise_changing_state(self) -> None:
        for kind in ("quest", PROMISE):
            for state in STATE_WORDS:
                change = prog.Change(prog.THREAD_STATE, "", "", state)
                ours = web.line_text(row("thread_state", state=state, threadType=kind))
                self.assertEqual(ours, thread_progress_card.line_text(change, {}, kind), state)

    def test_a_clue_found(self) -> None:
        names = {CLUE_ID: "the torn letter", "f1": "Ash", "f2": "Wren", "f3": "Cerric"}
        for known in (True, False):
            for finders in ((), ("f1",), ("f1", "f2"), ("f1", "f2", "f3")):
                change = prog.Change(
                    prog.CLUE, "", "a new clue", prog.FOUND, None, "",
                    CLUE_ID if known else None, finders,
                )  # fmt: skip
                ours = web.line_text(
                    row(
                        "clue", words="a new clue", clue="the torn letter" if known else None,
                        clueKnown=known, finders=[names[f] for f in finders],
                    )
                )  # fmt: skip
                self.assertEqual(
                    ours, thread_progress_card.line_text(change, names), (known, finders)
                )

    def test_part_kinds_cover_what_a_thread_has(self) -> None:
        every = {tp.OBJECTIVE, tp.COMPLICATION, tp.REWARD, tp.REWARD_GIVEN, tp.HELP, tp.TIME_LIMIT}
        self.assertEqual(set(web.KIND_WORDS), every)
