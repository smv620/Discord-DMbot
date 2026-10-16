"""Re-checking earlier lines after the DM's answer (#127), without Discord or a database: only
the DM's spelling for exactly the words they answered about, only lines still exactly as heard,
never a secret name, newest first and capped (docs/PLAN.md, "Learning is immediate")."""

import unittest
from typing import Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import FIX
from dmbot.transcript import fix_notes, refix
from dmbot.transcript.models import SIDEBAR_ANSWER, Line
from dmbot.transcript.questions import fixed_text
from tests.test_transcript_cleaner import (
    BELLEROS,
    CERRIC,
    MAREN,
    alias,
    correction,
    entity,
    lookup,
)

MIA, DEE = 8, 9


def line(at: int, heard: str, *, text: str | None = None, who: int = MIA, **kw: Any) -> Line:
    return Line(at, who, heard, heard if text is None else text, **kw)


def names() -> CampaignLookup:
    return lookup(corrections=(correction("Sara", CERRIC, FIX),))


class Planning(unittest.TestCase):
    def test_earlier_lines_with_the_words_are_written_with_the_dms_spelling_newest_first(
        self,
    ) -> None:
        lines = [
            line(1_000, "Sara draws her bow"),
            line(5_000, "then Sara waits"),
            line(3_000, "nothing here"),
        ]
        got = refix.plan(names(), lines, "Sara")
        self.assertEqual([r.started_ms for r in got], [5_000, 1_000])
        self.assertEqual([r.text for r in got], ["then Cerric waits", "Cerric draws her bow"])
        self.assertEqual(got[0].edits, ((5, 9, "Cerric"),))

    def test_a_line_that_is_not_exactly_as_heard_is_left(self) -> None:
        lines = [
            line(1, "Sara and Beleros", text="Sara and Belleros"),  # another fix is in it
            line(2, "Sara again", sidebar=SIDEBAR_ANSWER),  # DMbot's own answer line
            line(3, "Sara typed", typed=True),  # typed, not said
        ]
        self.assertEqual(refix.plan(names(), lines, "Sara"), [])

    def test_other_words_are_never_touched_and_nothing_is_guessed_by_sound(self) -> None:
        # Only a DM's spelling for exactly the words answered about; a sound match like
        # "Beleros" in a line that also says "Sara" is not made here.
        got = refix.plan(names(), [line(1, "Sara met Beleros")], "Sara")
        self.assertEqual([r.text for r in got], ["Cerric met Beleros"])
        self.assertEqual(refix.plan(names(), [line(1, "Beleros waits")], "Sara"), [])

    def test_the_words_twice_in_a_line_are_both_written(self) -> None:
        got = refix.plan(names(), [line(1, "Sara said Sara")], "Sara")
        self.assertEqual(got[0].text, "Cerric said Cerric")

    def test_never_where_it_would_make_a_secret_name(self) -> None:
        secret = lookup(
            more=(entity(MAREN, "Vane"),),
            more_aliases=(alias(MAREN, "Vane"), alias(BELLEROS, "Silas Vane", secret=True)),
            corrections=(correction("Bane", MAREN, FIX),),
        )
        lines = [line(1, "I met Silas Bane today"), line(2, "Bane waits")]
        got = refix.plan(secret, lines, "Bane")
        self.assertEqual([r.started_ms for r in got], [2])  # the line beside "Silas" is left
        self.assertEqual(got[0].text, "Vane waits")

    def test_a_table_persons_name_is_never_renamed(self) -> None:
        got = refix.plan(names(), [line(1, "Sara draws her bow")], "Sara", people=["Sara"])
        self.assertEqual(got, [])

    def test_at_most_the_limit_the_newest_first(self) -> None:
        lines = [line(i, "Sara waits") for i in range(1, 8)]
        got = refix.plan(names(), lines, "Sara", limit=3)
        self.assertEqual([r.started_ms for r in got], [7, 6, 5])

    def test_only_so_many_lines_are_cleaned(self) -> None:
        lines = [line(i, "Sara waits") for i in range(1, 8)]
        got = refix.plan(names(), lines, "Sara", max_cleaned=2)
        self.assertEqual([r.started_ms for r in got], [7, 6])

    def test_no_words_no_work(self) -> None:
        self.assertEqual(refix.plan(names(), [line(1, "Sara")], ""), [])


class Notes(unittest.TestCase):
    def test_one_undo_takes_back_the_answer_and_every_earlier_line_it_wrote(self) -> None:
        notes = fix_notes.FixNotes()
        for at in (1, 2, 3):
            notes.answered(fix_notes.Answer(42, MIA, at, "Sara waits", (), 0, 4, "Cerric"))
        self.assertEqual(notes.words_now(MIA, 2, "Sara waits", ()), "Cerric waits")
        back = []
        while (more := notes.take_back(42)) is not None:
            back.append(more)
        self.assertEqual([a.started_ms for a in back], [1, 2, 3])
        self.assertEqual(notes.words_now(MIA, 2, "Sara waits", ()), "Sara waits")

    def test_a_batch_is_kept_whole_or_not_at_all(self) -> None:
        notes = fix_notes.FixNotes()
        for batch in range(fix_notes.ANSWERS_KEPT + 3):
            for at in range(5):  # an answer and four earlier lines
                notes.answered(
                    fix_notes.Answer(
                        batch, DEE, batch * 10 + at, "Sara", (), 0, 4, "Cerric", at > 0
                    )
                )
        kept = {a.batch for a in notes.answers}
        self.assertEqual(len(kept), fix_notes.ANSWERS_KEPT)
        self.assertEqual(kept, set(range(3, fix_notes.ANSWERS_KEPT + 3)))  # the newest ones
        for whole in kept:
            self.assertEqual(len([a for a in notes.answers if a.batch == whole]), 5)

    def test_a_line_with_an_answer_or_a_note_is_known(self) -> None:
        notes = fix_notes.FixNotes()
        self.assertFalse(notes.on_line(MIA, 1))
        notes.answered(fix_notes.Answer(1, MIA, 1, "Sara", (), 0, 4, "Cerric", True))
        self.assertTrue(notes.on_line(MIA, 1))
        self.assertFalse(notes.on_line(MIA, 2))


class Words(unittest.TestCase):
    def test_a_long_tail_keeps_its_last_words_and_the_head_its_first(self) -> None:
        from dmbot.transcript.questions import first_words, last_words, seam_text

        tail = last_words("so yesterday evening at the old inn near the harbour we met Ka", 4)
        self.assertEqual(tail, "harbour we met Ka")
        self.assertEqual(first_words("Zeth at dawn and then", 3), "Zeth at dawn")
        text = seam_text("Mia", tail, "Zeth at dawn")
        self.assertIn('"…harbour we met Ka"', text)
        self.assertIn("This joins two lines", text)


class Copies(unittest.TestCase):
    """The helpers' copies of a piece follow a change (#127): the sidebar's recent scene and the
    off-topic filter's window."""

    def test_the_recent_scene_says_the_new_words_only_for_a_single_certain_copy(self) -> None:
        from dmbot.sidebar.service import Recent

        recent = Recent.new()
        recent.add(MIA, "we meet Ka", 1.0)
        recent.add(DEE, "we meet Ka", 2.0)
        self.assertTrue(recent.revise(MIA, "we meet Ka", "we meet Ka'zeth"))
        self.assertEqual(
            [(u, t) for _, u, t in recent.lines], [(MIA, "we meet Ka'zeth"), (DEE, "we meet Ka")]
        )
        self.assertEqual(len(recent.spoken), 2)
        # The same person said the same words twice: it can't tell which, so it changes neither.
        twice = Recent.new()
        twice.add(MIA, "we meet Ka", 1.0)
        twice.add(MIA, "we meet Ka", 2.0)
        self.assertFalse(twice.revise(MIA, "we meet Ka", "we meet Ka'zeth"))
        self.assertEqual([t for _, _, t in twice.lines], ["we meet Ka", "we meet Ka"])
        self.assertFalse(twice.revise(MIA, "never said", "x"))

    def test_the_waiting_window_asks_the_ai_about_the_new_words(self) -> None:
        from dmbot.transcript.topics import TopicWindow, Waiting

        window = TopicWindow()
        window.add(Waiting(MIA, 5, "we meet Ka", 1.0), 0.0)
        self.assertTrue(window.revise(MIA, 5, "we meet Ka'zeth"))
        self.assertEqual([w.text for w in window.lines], ["we meet Ka'zeth"])
        self.assertFalse(window.revise(MIA, 6, "x"))  # not waiting (already taken)


class Saying(unittest.TestCase):
    def test_the_answer_says_how_many_earlier_lines_were_fixed(self) -> None:
        self.assertIn("Earlier lines stay as heard.", fixed_text("Sara", "Cerric", line_fixed=True))
        one = fixed_text("Sara", "Cerric", line_fixed=True, earlier=1)
        self.assertIn("1 earlier line was fixed too.", one)
        self.assertNotIn("stay as heard", one)
        many = fixed_text("Sara", "Cerric", earlier=4)
        self.assertIn("4 earlier lines were fixed too.", many)


if __name__ == "__main__":
    unittest.main()
