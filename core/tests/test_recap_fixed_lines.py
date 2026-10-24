"""The fixed opening and closing lines of every recap (#2253): added by code at the end, never
counted as story, said by the narration, never a secret name, and added once. Invented fixtures."""

from __future__ import annotations

import unittest
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.recaps import fixed_lines as fl
from dmbot.recaps.audio import script
from dmbot.recaps.draft import STORY_MAX, SUMMARY_MAX, WORDS_CEILING
from dmbot.recaps.text import narration_of, words
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest

NAME = "The Ember Road"
BARE_CLOSING = fl.CLOSING_BARE.format(ending=fl.ENDINGS[0])


class TheLines(unittest.TestCase):
    def test_the_campaigns_name_is_in_both_lines(self) -> None:
        opening, closing = fl.lines(NAME)
        self.assertEqual(opening, "Previously, on The Ember Road…")
        self.assertEqual(closing, "And so we continue, the epic adventure of The Ember Road.")

    def test_a_secret_name_is_never_spoken_the_lines_go_without_a_name(self) -> None:
        for secret in (("The Ember Road",), ("ember road",), ("Ember",)):
            with self.subTest(secret=secret):
                self.assertEqual(
                    fl.lines(NAME, secret),
                    (fl.OPENING_BARE, BARE_CLOSING),
                )
        self.assertEqual(fl.lines("", ()), (fl.OPENING_BARE, BARE_CLOSING))
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
            paragraphs[-1], "And so we continue, the epic adventure of The Ember Road."
        )
        self.assertEqual(paragraphs[1:-1], fx.SUMMARY.split("\n\n"))

    def test_wrapping_twice_or_a_text_with_a_line_already_changes_nothing(self) -> None:
        once = fl.wrap(fx.SUMMARY, NAME)
        self.assertEqual(fl.wrap(once, NAME), once)
        closed = fx.SUMMARY + "\n\nAnd so we continue, the epic quest of Somewhere."
        only_opening = fl.wrap(closed, NAME)
        self.assertEqual(only_opening.count("And so we continue"), 1)
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
        self.assertIn("And so we continue, the epic adventure of The Ember Road", spoken)

    def test_the_editor_has_room_for_the_longest_draft_with_both_lines(self) -> None:
        opening, closing = fl.lines("x" * 100)  # the story is shortened to STORY_MAX (#2597)
        self.assertLessEqual(STORY_MAX + len(opening) + len(closing) + 4, SUMMARY_MAX)
        self.assertGreater(WORDS_CEILING, 0)

    def test_a_story_that_only_reads_like_the_lines_is_left_alone(self) -> None:
        story = "Previously on the road they walked… and on.\n\nMore."
        self.assertEqual(fl.without(story), story)
        self.assertTrue(fl.wrap(story, NAME).startswith("Previously, on The Ember Road…"))
        ending = "The tale ends: And so we continue, the epic quest of the Lost Mine."
        self.assertEqual(fl.without(ending), ending)  # a sentence is not the closing paragraph
        self.assertTrue(fl.wrap(ending, NAME).endswith("epic adventure of The Ember Road."))

    def test_retyped_dots_still_count_and_a_name_cannot_break_the_shape(self) -> None:
        retyped = (
            "Previously, on The Ember Road...\n\nStory.\n\nAnd so we continue, the grand adventure."
        )
        self.assertEqual(fl.without(retyped), "Story.")
        self.assertEqual(fl.usable_name("Road… of Doom...", ()), "Road of Doom")
        self.assertEqual(fl.without(fl.wrap("Story.", "Dots… and (cues)")), "Story.")


class TheTenEndings(unittest.TestCase):
    def test_the_list_keeps_the_owners_order(self) -> None:
        self.assertEqual(len(fl.ENDINGS), 10)
        self.assertEqual(fl.ENDINGS[0], "epic adventure")
        self.assertEqual(fl.ENDINGS[-1], "epic quest")

    def test_each_variant_is_found_and_neighbouring_sessions_end_differently(self) -> None:
        seen = []
        for earlier in range(12):  # past ten, so the rotation wraps
            index = fl.ending_for(earlier)
            wrapped = fl.wrap(fx.SUMMARY, NAME, (), index)
            self.assertEqual(fl.without(wrapped), fx.SUMMARY)  # the finder finds this variant
            self.assertIn(fl.ENDINGS[index], wrapped.split("\n\n")[-1])
            seen.append(index)
        self.assertEqual(seen[:10], list(range(10)))
        self.assertEqual(seen[10:], [0, 1])
        self.assertTrue(all(a != b for a, b in pairwise(seen)))

    def test_a_garbled_ending_is_still_found(self) -> None:
        garbled = "Story.\n\nAnd so we continue the gret advnture of Ember Road!"
        self.assertEqual(fl.without(garbled), "Story.")

    def test_a_campaign_named_like_an_ending_is_no_trouble(self) -> None:
        wrapped = fl.wrap("Story.", "The Epic Quest", (), 3)
        self.assertTrue(wrapped.endswith("the grand adventure of The Epic Quest."))
        self.assertEqual(fl.without(wrapped), "Story.")

    def test_the_first_drafts_wording_is_not_the_closing_line(self) -> None:
        old = "Story.\n\nAnd so the party continues the adventure of Somewhere."
        self.assertEqual(fl.without(old), old)

    def test_no_name_still_ends_with_the_chosen_variant(self) -> None:
        self.assertEqual(fl.lines("", (), 5)[1], "And so we continue, the epic tale.")


class TheEndingByPlace(DatabaseTest):
    """Through `load_inputs` on a real database: the ending follows the session's place in the
    campaign, whatever was posted, drafted or imported."""

    async def test_endings_run_round_the_ten_whatever_was_posted(self) -> None:
        from dmbot.campaigns.store import CampaignStore
        from dmbot.memory.lookup import LookupCache
        from dmbot.memory.store import MemoryStore
        from dmbot.recaps.inputs import load_inputs
        from dmbot.recaps.store import RecapStore
        from dmbot.transcript.store import TranscriptStore

        campaigns = CampaignStore(self.db)
        camp = await campaigns.create(1, "One", fx.DM)
        transcripts = TranscriptStore(self.db)
        recaps = RecapStore(self.db)
        starts = [1_700_000_000 + n * 86_400 for n in range(11)]
        for at in starts:
            sid = await transcripts.open_session(1, camp.id, at)
            await transcripts.add_lines(1, sid, fx.lines())
        # An unposted draft before them (and nothing posted at all) changes nothing.
        await recaps.enqueue(1, camp.id, starts[1], dm_user_id=fx.DM)

        async def ending_at(at: int) -> int:
            got = await load_inputs(
                campaigns=campaigns, lookup=LookupCache(MemoryStore(self.db)),
                transcripts=transcripts, recaps=recaps, clocks=None, guild_id=1,
                campaign_id=camp.id, session_at=at,
            )  # fmt: skip
            assert got is not None
            return got.ending

        self.assertEqual([await ending_at(at) for at in starts[:3]], [0, 1, 2])
        self.assertEqual(await ending_at(starts[10]), 0)  # the eleventh wraps
        self.assertEqual(await ending_at(starts[9]), 9)


class TheNextRecapIsNotShownThem(unittest.IsolatedAsyncioTestCase):
    async def test_last_times_recap_reaches_the_writer_without_the_fixed_lines(self) -> None:
        from dmbot.recaps.inputs import load_inputs

        bridge = SimpleNamespace(summary=fl.wrap("《Calm》 The keep fell quiet.", NAME))
        session = SimpleNamespace(started_at=200, id="s")
        before = [SimpleNamespace(started_at=t, id=f"e{t}") for t in (50, 120)]
        got = await load_inputs(
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(name=NAME, dm_user_ids={7}))
            ),
            lookup=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(names=[]))),
            transcripts=SimpleNamespace(
                latest_before=AsyncMock(return_value=(120, False)),
                sessions=AsyncMock(return_value=[session, *before]),
                lines=AsyncMock(return_value=[]),
            ),
            recaps=SimpleNamespace(
                bridge=AsyncMock(return_value=bridge), earlier_job=AsyncMock(return_value=None)
            ),
            clocks=None,
            guild_id=1,
            campaign_id="c" * 32,
            session_at=200,
        )
        assert got is not None
        self.assertEqual(got.previous, "The keep fell quiet.")
        self.assertEqual(got.ending, 2)  # two sessions came before this one
        self.assertEqual(got.campaign_name, NAME)  # and this recap's lines will carry the name
