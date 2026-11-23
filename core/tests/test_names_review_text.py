"""The after-session name review's wording (#394), without Discord or a database."""

import unittest
from typing import Any

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


class TheHeardLine(unittest.TestCase):
    """#5133 slice 3c: one name heard several ways, in the words Supervisor ruled."""

    def test_spoken_once_and_several_ways(self) -> None:
        self.assertEqual(ui.heard_line(1, 0, []), "Heard once.")
        self.assertEqual(ui.heard_line(0, 0, []), "Heard once.")
        self.assertEqual(
            ui.heard_line(5, 0, ["Rothgar", "hroth gar"]),
            "Heard 5 times, sometimes as *Rothgar* or *hroth gar*.",
        )

    def test_at_most_three_other_spellings_then_how_many_more(self) -> None:
        got = ui.heard_line(9, 0, ["A1", "B1", "C1", "D1", "E1"])
        self.assertEqual(got, "Heard 9 times, sometimes as *A1*, *B1* or *C1* and 2 more.")

    def test_typed_says_how_often_it_was_typed_and_heard(self) -> None:
        self.assertEqual(
            ui.heard_line(4, 1, ["Rothgar"]),
            "Typed once in chat, and heard 3 times, sometimes as *Rothgar*.",
        )
        self.assertEqual(ui.heard_line(1, 1, []), "Typed once in chat.")

    def test_typed_never_loses_the_hearings_or_a_spelling(self) -> None:
        self.assertEqual(ui.heard_line(4, 1, []), "Typed once in chat, and heard 3 times.")
        self.assertEqual(
            ui.heard_line(2, 2, ["Rothgar"]), "Typed 2 times in chat, sometimes as *Rothgar*."
        )
        self.assertEqual(ui.heard_line(1, 3, []), "Typed once in chat.")  # (never more than heard)

    def test_the_dm_s_own_text_is_found_not_heard(self) -> None:
        self.assertEqual(ui.heard_line(1, 0, [], written=True), "Found once in your text.")
        self.assertEqual(
            ui.heard_line(2, 0, ["Rothgar"], written=True),
            "Found 2 times in your text, sometimes as *Rothgar*.",
        )

    def test_a_spelling_with_markdown_is_escaped(self) -> None:
        self.assertIn(r"*Roth\*gar*", ui.heard_line(2, 0, ["Roth*gar"]))

    def test_the_card_asks_on_its_title_line_when_what_made_it_a_name_was_kept(self) -> None:
        kept = ui.suggestion_text(
            suggested("Hrothgar", "Heard 5 times"), 1, also=["Rothgar"], counts=(5, 0)
        )
        self.assertTrue(
            kept.startswith("📝 **Hrothgar**: a new name? Heard 5 times, sometimes as *Rothgar*.")
        )
        self.assertNotIn("Also heard as", kept)
        self.assertNotIn("Is this a name in your game?", kept)
        self.assertIn("Pick what it is to add it, or press 🚫 Not a name.", kept)  # (no guess)
        today = ui.suggestion_text(suggested("Hrothgar"), 1, also=["Rothgar"])
        self.assertIn("Also heard as **Rothgar**: saved with it.", today)
        self.assertIn("Is this a name in your game?", today)

    def test_a_card_with_its_own_question_keeps_it_under_the_heard_line(self) -> None:
        got = ui.suggestion_text(
            suggested("Hrothgar"), 1, match=Match("e1", "Hrothgard"), counts=(2, 0)
        )
        self.assertIn("**Hrothgar**\nHeard 2 times.", got)
        self.assertIn("Sounds like **Hrothgard**. The same, or new?", got)
        spelled = ui.suggestion_text(suggested("Hrothgar"), 1, fix_to="Hrothgarr", counts=(3, 1))
        self.assertIn("\nTyped once in chat, and heard 2 times.", spelled)
        self.assertIn("Someone spelled it out: **Hrothgarr**.", spelled)


class TheOrder(unittest.TestCase):
    def found(self, how: str, times: int = 0) -> Any:
        from dmbot.memory.name_found import Found

        return Found(how, None, 0, times)

    def test_introduced_or_typed_first_then_most_heard_then_the_rest_as_made(self) -> None:
        a, b = suggested("Vorna", "Heard 9 times"), suggested("Ostrin", "Heard 2 times")
        c, d = suggested("Tamsin", "Heard 4 times"), suggested("Garreth", "Heard 7 times")
        found = {
            "a": self.found("spoken_of"),
            "b": self.found("introduced"),
            "c": self.found("scan"),
        }
        keyed = [(a, "a"), (b, "b"), (c, "c"), (d, "d")]
        ordered = sorted(keyed, key=lambda p: ui._surest(p[0], found.get(p[1])))
        self.assertEqual([e.name for e, _k in ordered], ["Ostrin", "Vorna", "Tamsin", "Garreth"])

    def test_the_kept_count_wins_over_the_note(self) -> None:
        a, b = suggested("Vorna", "Heard 9 times"), suggested("Ostrin", "Heard 2 times")
        found = {"a": self.found("scan", 3), "b": self.found("scan", 6)}
        ordered = sorted([(a, "a"), (b, "b")], key=lambda p: ui._surest(p[0], found.get(p[1])))
        self.assertEqual([e.name for e, _k in ordered], ["Ostrin", "Vorna"])

    def test_a_tie_is_in_the_order_made(self) -> None:
        import dataclasses

        a = dataclasses.replace(suggested("Vorna", "Heard 2 times"), created_at=20)
        b = dataclasses.replace(suggested("Ostrin", "Heard 2 times"), created_at=10)
        found = self.found("scan")
        ordered = sorted([a, b], key=lambda e: ui._surest(e, found))
        self.assertEqual([e.name for e in ordered], ["Ostrin", "Vorna"])


if __name__ == "__main__":
    unittest.main()
