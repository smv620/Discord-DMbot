"""The after-session name review's wording (#394), without Discord or a database."""

import unittest

from dmbot.memory.models import PROPOSED, Entity
from dmbot.memory.scan import Match
from dmbot.ui import names as ui


def suggested(name: str, description: str = "Heard 3 times") -> Entity:
    return Entity("a" * 32, "concept", name, description, PROPOSED, None, "scan", 0)


class ReviewTextTest(unittest.TestCase):
    def test_with_a_match(self) -> None:
        text = ui.suggestion_text(
            suggested("Rothgr"), 3, also=["Vane"], match=Match("b" * 32, "Hrothgar")
        )
        self.assertIn("📝 **Rothgr** · heard 3 times", text)
        self.assertIn("Also heard as **Vane**: saved with it.", text)
        self.assertIn("Sounds like **Hrothgar**. The same, or new?", text)
        self.assertIn("2 more names after this one", text)

    def test_without_a_match(self) -> None:
        text = ui.suggestion_text(suggested("Hrothgar"), 1)
        self.assertIn("Is this a name in your game?", text)
        self.assertNotIn("Also heard", text)

    def test_the_sentence_around_a_name_from_the_dms_text_is_shown_when_held(self) -> None:
        text = ui.suggestion_text(suggested("Greyfen"), 1, context="The abbot of Greyfen waits.")
        self.assertIn("Around it in your text: \u201cThe abbot of Greyfen waits.\u201d", text)
        self.assertNotIn("Around it", ui.suggestion_text(suggested("Greyfen"), 1))

    def test_only_a_dm_of_the_campaign_sees_the_sentence(self) -> None:
        from types import SimpleNamespace

        from dmbot.memory.lore_context import Held

        held = Held()
        held.put(5, "a" * 32, "The abbot secretly pays them.")
        client = SimpleNamespace(lore_context=held)
        campaign = SimpleNamespace(
            guild_id=5, dm_user_ids=frozenset({9}), dm_accounts=frozenset({9})
        )

        def seen(user_id: int) -> str:
            who = SimpleNamespace(client=client, user=SimpleNamespace(id=user_id))
            return ui.SuggestionReview._context(who, campaign, suggested("Greyfen"))  # type: ignore[arg-type]

        self.assertEqual(seen(9), "The abbot secretly pays them.")
        self.assertEqual(seen(10), "")  # a server manager who is not a DM

    def test_the_players_line_is_shown_under_the_name_whole_and_only_when_there_is_one(
        self,
    ) -> None:
        line = "a" * 120
        text = ui.suggestion_text(suggested("Ulfgar"), 1, players=line)
        self.assertIn(f"\nPlayers know: {line}", text)  # whole: never cut on the card
        self.assertLess(text.index("Players know"), text.index("Is this a name"))
        self.assertTrue(text.index("📝 **Ulfgar**") < text.index("Players know"))
        self.assertNotIn("Players know", ui.suggestion_text(suggested("Ulfgar"), 1))
        self.assertNotIn("Players know", ui.suggestion_text(suggested("Ulfgar"), 1, players=""))
        formatted = ui.suggestion_text(suggested("Ulfgar"), 1, players="*loud* _mood_")
        self.assertIn(r"Players know: \*loud\* \_mood\_", formatted)  # as written, not formatting
        mention = ui.suggestion_text(suggested("Ulfgar"), 1, players="@everyone <@123>")
        self.assertIn("@everyone", mention)  # kept: the card is sent with no pings allowed

    def test_the_sentence_around_the_name_goes_before_the_line_is_ever_cut(self) -> None:
        around = "y" * (ui.SUGGESTION_MAX + 50)
        text = ui.suggestion_text(
            suggested("Ulfgar"), 1, players="Smith of the town.", context=around
        )
        self.assertIn("Players know: Smith of the town.", text)
        self.assertNotIn(around, text)
        self.assertLessEqual(len(text), ui.SUGGESTION_MAX)

    def test_the_line_stays_when_a_long_scene_is_the_part_left_out(self) -> None:
        scene = "x" * (ui.SUGGESTION_MAX + 50)
        text = ui.suggestion_text(suggested("Ulfgar"), 1, players="Smith of the town.", scene=scene)
        self.assertIn("Players know: Smith of the town.", text)
        self.assertNotIn(scene, text)

    def test_two_rows_so_a_phone_never_cuts_the_labels(self) -> None:
        view = ui.SuggestionReview("c" * 32, ["a" * 32])
        view.match = Match("b" * 32, "Hrothgar")
        view.refs = [("s" * 32, 12)]  # somewhere it was said, so the lookup button shows
        view._buttons()
        rows = [(b.label, b.row) for b in view.children]  # type: ignore[attr-defined]
        self.assertEqual(
            rows,
            [
                ("✅ It's Hrothgar", 0),
                ("➕ New name", 0),
                ("🔗 Another known name…", 0),
                ("🚫 Not a name", 1),
                ("⏳ Later", 1),
                ("✏️ Change the words", 1),
                # the lookup button has a row of its own, below the two answer rows (#3384)
                ("📜 Where was it said?", 2),
            ],
        )
        view.match = None
        view._buttons()
        # with no kind guessed, the picker is the first row (#2937), the buttons below it
        self.assertEqual([b.row for b in view.children], [0, 1, 1, 1, 1, 2])

    def test_where_was_it_said_shows_only_with_something_behind_it(self) -> None:
        # #4227: a name with no kept place and no held sentence from an upload has no lookup
        view = ui.SuggestionReview("c" * 32, ["a" * 32])
        view._buttons()
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertNotIn("📜 Where was it said?", labels)
        view.lore_kept = True
        view._buttons()
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertIn("📜 Where was it said?", labels)

    def test_names_are_shown_as_written(self) -> None:
        text = ui.suggestion_text(suggested("*Star*"), 1, match=Match("b" * 32, "*Sun*"))
        self.assertIn("\\*Star\\*", text)
        self.assertIn("\\*Sun\\*", text)

    def test_its_label_fits_a_phone(self) -> None:
        self.assertEqual(ui.its_label("Hrothgar"), "✅ It's Hrothgar")
        long = ui.its_label("Lord Dagult Neverember the Third")
        self.assertLessEqual(len(long), ui.REVIEW_LABEL_MAX)
        self.assertTrue(long.startswith("✅ It's Lord"))


class FoundNoteTest(unittest.TestCase):
    def test_a_shorter_list_is_never_a_silent_one(self) -> None:
        from dmbot.ui.name_background import _found_note

        self.assertEqual(_found_note(3, 3), "")
        self.assertEqual(_found_note(0, 0), "")
        note = _found_note(21, 3)
        self.assertIn("Found 21 connections: 3 ready to check", note)
        self.assertIn("18 not read in time", note)  # nothing is lost without a number
        self.assertIn("keeps one only when both ends are names", note)
        self.assertIn("Found 1 connection:", _found_note(1, 0))

    def test_the_note_never_prints_an_empty_list(self) -> None:
        from dmbot.ui.name_background import _found_note

        for found, kept in ((4, 0), (4, 1), (9, 2)):
            note = _found_note(found, kept)
            self.assertNotIn(": .", note)
            self.assertNotIn(": ,", note)

    def test_the_note_is_also_said_when_no_name_is_new(self) -> None:
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.ui.name_background import summary

        interaction = SimpleNamespace(edit_original_response=AsyncMock())
        campaign = SimpleNamespace(id="c" * 32)
        asyncio.run(summary(interaction, campaign, "Notes", {}, 0, links_found=4))  # type: ignore[arg-type]
        text = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("found no new names", text)
        self.assertIn("Found 4 connections: 4 not read in time", text)


if __name__ == "__main__":
    unittest.main()
