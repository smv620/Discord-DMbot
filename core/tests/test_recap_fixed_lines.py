"""The fixed opening and closing lines of every recap (#2253): added by code at the end, never
counted as story, said by the narration, never a secret name, and added once. Invented fixtures."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.recaps import fixed_lines as fl
from dmbot.recaps.audio import script
from dmbot.recaps.draft import SUMMARY_MAX, WORDS_CEILING, length_for
from dmbot.recaps.text import narration_of, words
from tests import recap_fixtures as fx

NAME = "The Ember Road"


class TheLines(unittest.TestCase):
    def test_the_campaigns_name_is_in_both_lines(self) -> None:
        opening, closing = fl.lines(NAME)
        self.assertEqual(opening, "Previously, on The Ember Road…")
        self.assertEqual(closing, "And so the party continues the adventure of The Ember Road.")

    def test_a_secret_name_is_never_spoken_the_lines_go_without_a_name(self) -> None:
        for secret in (("The Ember Road",), ("ember road",), ("Ember",)):
            with self.subTest(secret=secret):
                self.assertEqual(fl.lines(NAME, secret), (fl.OPENING_BARE, fl.CLOSING_BARE))
        self.assertEqual(fl.lines("", ()), (fl.OPENING_BARE, fl.CLOSING_BARE))
        # a secret that is not this name changes nothing
        self.assertEqual(fl.lines(NAME, ("Vecnor",))[0], "Previously, on The Ember Road…")

    def test_cue_brackets_and_line_breaks_in_a_name_cannot_break_the_text(self) -> None:
        self.assertEqual(fl.usable_name("《Dark》 Road ((x))\n of Doom", ()), "Dark Road x of Doom")
        self.assertLessEqual(len(fl.usable_name("x" * 500, ())), 100)

    def test_the_text_gets_one_line_at_each_end_as_its_own_paragraph(self) -> None:
        wrapped = fl.wrap(fx.SUMMARY, NAME)
        paragraphs = wrapped.split("\n\n")
        self.assertEqual(paragraphs[0], "Previously, on The Ember Road…")
        self.assertEqual(
            paragraphs[-1], "And so the party continues the adventure of The Ember Road."
        )
        self.assertEqual(paragraphs[1:-1], fx.SUMMARY.split("\n\n"))

    def test_wrapping_twice_or_a_text_with_a_line_already_changes_nothing(self) -> None:
        once = fl.wrap(fx.SUMMARY, NAME)
        self.assertEqual(fl.wrap(once, NAME), once)
        closed = fx.SUMMARY + "\n\nAnd so the party continues the adventure of Somewhere."
        only_opening = fl.wrap(closed, NAME)
        self.assertEqual(only_opening.count("And so the party"), 1)
        self.assertTrue(only_opening.startswith("Previously, on The Ember Road…"))

    def test_without_takes_both_lines_off_and_leaves_the_story(self) -> None:
        for name in (NAME, ""):
            with self.subTest(name=name):
                self.assertEqual(fl.without(fl.wrap(fx.SUMMARY, name)), fx.SUMMARY.strip())
        self.assertEqual(fl.without(fx.SUMMARY), fx.SUMMARY.strip())  # an old recap is unchanged

    def test_the_lines_are_outside_the_word_count_and_the_narration_says_them(self) -> None:
        wrapped = fl.wrap(fx.SUMMARY, NAME)
        story = len(words(narration_of(fl.without(wrapped))))
        self.assertEqual(story, len(words(narration_of(fx.SUMMARY))))
        self.assertGreater(len(words(narration_of(wrapped))), story)
        built = script.build("The Silver Lock", 4, wrapped)
        spoken = " ".join(p.spoken for p in built.paragraphs)
        self.assertIn("Previously, on The Ember Road", spoken)
        self.assertIn("And so the party continues the adventure of The Ember Road", spoken)

    def test_the_editor_has_room_for_the_longest_draft_with_both_lines(self) -> None:
        longest = round(length_for(8).last_high) * 8  # the most a draft may be, in characters
        opening, closing = fl.lines("x" * 100)
        self.assertLessEqual(longest + len(opening) + len(closing) + 4, SUMMARY_MAX)
        self.assertGreater(WORDS_CEILING, 0)

    def test_a_story_that_only_reads_like_the_lines_is_left_alone(self) -> None:
        story = "Previously on the road they walked… and on.\n\nMore."
        self.assertEqual(fl.without(story), story)
        self.assertTrue(fl.wrap(story, NAME).startswith("Previously, on The Ember Road…"))
        ending = "The tale ends: And so the party continues the adventure of the Lost Mine."
        self.assertEqual(fl.without(ending), ending)  # a sentence is not the closing paragraph
        self.assertTrue(fl.wrap(ending, NAME).endswith("adventure of The Ember Road."))

    def test_retyped_dots_still_count_and_a_name_cannot_break_the_shape(self) -> None:
        retyped = (
            "Previously, on The Ember Road...\n\nStory.\n\n"
            "And so the party continues the adventure."
        )
        self.assertEqual(fl.without(retyped), "Story.")
        self.assertEqual(fl.usable_name("Road… of Doom...", ()), "Road of Doom")
        self.assertEqual(fl.without(fl.wrap("Story.", "Dots… and (cues)")), "Story.")


class TheNextRecapIsNotShownThem(unittest.IsolatedAsyncioTestCase):
    async def test_last_times_recap_reaches_the_writer_without_the_fixed_lines(self) -> None:
        from dmbot.recaps.inputs import load_inputs

        bridge = SimpleNamespace(summary=fl.wrap("《Calm》 The keep fell quiet.", NAME))
        session = SimpleNamespace(started_at=200, id="s")
        got = await load_inputs(
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(name=NAME, dm_user_ids={7}))
            ),
            lookup=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(names=[]))),
            transcripts=SimpleNamespace(
                sessions=AsyncMock(return_value=[session]), lines=AsyncMock(return_value=[])
            ),
            recaps=SimpleNamespace(bridge=AsyncMock(return_value=bridge)),
            clocks=None,
            guild_id=1,
            campaign_id="c" * 32,
            session_at=200,
        )
        assert got is not None
        self.assertEqual(got.previous, "The keep fell quiet.")
        self.assertEqual(got.campaign_name, NAME)  # and this recap's lines will carry the name
