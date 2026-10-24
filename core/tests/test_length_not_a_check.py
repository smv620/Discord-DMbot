"""The rest of the length rule (#2601): a hard limit shortens at a word end and keeps the item;
it never drops it. A check about truth (a quote must be in the text) stays. Pure unless noted."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot import images
from dmbot.campaigns.store import CampaignStore
from dmbot.memory import background as bg
from dmbot.memory import claims, sheets, story_plan
from dmbot.recaps import feedback
from dmbot.recaps.fixed_lines import STORY_COLUMN_MAX
from dmbot.recaps.store import RecapStore
from dmbot.timebot import suggest
from dmbot.web import support_ai
from dmbot.word_end import at_word_end
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest

LONG = "word " * 400  # 2,000 characters


class WordEnd(unittest.TestCase):
    def test_edges(self) -> None:
        self.assertEqual(at_word_end("one two", 7), "one two")
        self.assertEqual(at_word_end("one two", 5), "one")
        self.assertEqual(at_word_end("one  two", 4), "one")
        self.assertEqual(at_word_end("one\ntwo three", 8), "one\ntwo")  # a line break ends a word
        self.assertEqual(at_word_end("abcdefghij", 4), "abcd")
        self.assertEqual(at_word_end("anything", 0), "")


class Kept(unittest.TestCase):
    def test_claims_fields_are_shortened_not_dropped(self) -> None:
        got = claims._field(LONG)
        assert got is not None
        self.assertLessEqual(len(got), claims.FIELD_MAX)
        self.assertTrue(got.endswith("word"))

    def test_story_plan_text_is_shortened_not_dropped(self) -> None:
        got = story_plan._clean(LONG, 120)
        assert got is not None
        self.assertLessEqual(len(got), 120)
        self.assertTrue(got.endswith("word"))

    def test_a_sheet_name_over_the_limit_is_kept_shortened(self) -> None:
        got = sheets._name(LONG)
        assert got is not None
        self.assertLessEqual(len(got), sheets.NAME_MAX)

    def test_image_text_is_shortened_at_a_word_end(self) -> None:
        got = images.clean(LONG, 300)
        self.assertLessEqual(len(got), 300)
        self.assertTrue(got.endswith("word"))

    def test_a_long_feedback_point_is_kept_shortened(self) -> None:
        answer = json.dumps({"points": [{"kind": "other", "text": LONG}]})
        (point,) = feedback.parse(answer) or []
        self.assertLessEqual(len(point.text), feedback.POINT_CHARS)
        self.assertTrue(point.text.endswith("word"))

    def test_timebots_reason_is_shortened_at_a_word_end(self) -> None:
        got = suggest._clean_why("a rather long reason indeed " * 5)
        self.assertLessEqual(len(got), suggest.WHY_MAX)

    def test_the_support_reply_goes_out_whole_up_to_what_the_thread_keeps(self) -> None:
        answer = ("A sentence of the reply. " * 200).strip()  # about 5,000 characters
        verdict = support_ai.read_verdict(json.dumps({"answer": answer, "verdict": "happy"}))
        assert verdict is not None
        self.assertEqual(verdict.answer, answer)


class LoreConnections(unittest.TestCase):
    TEXT = "Mira, the daughter of Lord Veil, " + "walked on and on and on, " * 12 + "far away."

    def connection(self, **kw: Any) -> list[bg.Connection]:
        item = {
            "a": "Mira",
            "b": "Lord Veil",
            "how": "family",
            "quote": self.TEXT,
        } | kw
        return bg.read_connections(json.dumps({"connections": [item]}), self.TEXT)

    def test_a_long_quote_is_shortened_to_the_part_that_holds_both_names(self) -> None:
        (found,) = self.connection()
        self.assertLessEqual(len(found.quote.split()), bg.QUOTE_WORDS)
        self.assertIn("Mira", found.quote)
        self.assertIn("Lord Veil", found.quote)
        self.assertIn(found.quote, self.TEXT)  # still verbatim

    def test_a_quote_that_is_not_in_the_text_is_still_refused(self) -> None:
        self.assertEqual(self.connection(quote="Mira and Lord Veil are never in this text"), [])

    def test_a_long_label_is_shortened_not_dropped(self) -> None:
        text = "Mira served Lord Veil at the old keeper of the high western gate " * 3
        reply = {"a": "Mira", "b": "Lord Veil", "how": "other", "quote": text, "words": text}
        found = bg.read_connections(json.dumps({"connections": [reply]}), text)
        self.assertEqual(len(found), 1)
        self.assertLessEqual(len(found[0].words), bg.WORDS_CHARS)


class StoryColumn(unittest.TestCase):
    def test_the_column_limit_is_the_one_the_code_shortens_to(self) -> None:
        from dmbot import schema
        from dmbot.recaps import draft

        self.assertEqual(draft.SUMMARY_MAX, STORY_COLUMN_MAX)
        self.assertIn(f"char_length(summary) <= {STORY_COLUMN_MAX}", schema.RECAP_STORY_ROOM)
        self.assertIn(f"char_length(original) <= {STORY_COLUMN_MAX}", schema.RECAP_STORY_ROOM)

    def test_a_story_of_6000_characters_is_not_cut(self) -> None:
        from dmbot.recaps import draft

        story = "\n\n".join(["《Calm》 " + "Mira walked on. " * 20] * 20)  # about 7,000 characters
        self.assertGreater(len(story), 6000)
        self.assertEqual(draft.fit_summary(story), story)

    def test_a_huge_story_is_shortened_and_logged_as_a_guard(self) -> None:
        from dmbot.recaps import draft

        story = "\n\n".join(["《Calm》 " + "Mira walked on. " * 200] * 40)
        with self.assertLogs("dmbot.recaps.draft", "WARNING"):
            got = draft.fit_summary(story)
        self.assertLessEqual(len(got), draft.STORY_MAX)


class StoryInTheDatabase(DatabaseTest):
    async def test_a_story_over_6000_characters_is_stored_whole(self) -> None:
        campaigns = CampaignStore(self.db)
        one = await campaigns.create(1, "One", fx.DM)
        store = RecapStore(self.db)
        await store.enqueue(1, one.id, 100, dm_user_id=fx.DM)
        story = "《Calm》 " + "Mira walked on. " * 1200  # about 19,000 characters
        await store.update(1, one.id, 100, summary=story, original=story)
        job = await store.get(1, one.id, 100)
        assert job is not None
        self.assertEqual((job.summary, job.original), (story, story))
