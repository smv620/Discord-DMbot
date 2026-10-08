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

    def test_two_rows_so_a_phone_never_cuts_the_labels(self) -> None:
        view = ui.SuggestionReview("c" * 32, ["a" * 32])
        view.match = Match("b" * 32, "Hrothgar")
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
            ],
        )
        view.match = None
        view._buttons()
        self.assertEqual([b.row for b in view.children], [0, 0, 1, 1])

    def test_names_are_shown_as_written(self) -> None:
        text = ui.suggestion_text(suggested("*Star*"), 1, match=Match("b" * 32, "*Sun*"))
        self.assertIn("\\*Star\\*", text)
        self.assertIn("\\*Sun\\*", text)

    def test_its_label_fits_a_phone(self) -> None:
        self.assertEqual(ui.its_label("Hrothgar"), "✅ It's Hrothgar")
        long = ui.its_label("Lord Dagult Neverember the Third")
        self.assertLessEqual(len(long), ui.REVIEW_LABEL_MAX)
        self.assertTrue(long.startswith("✅ It's Lord"))


if __name__ == "__main__":
    unittest.main()
