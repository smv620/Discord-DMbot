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
            suggested("Rothgr"), 3, also=["Vane"], match=Match("b" * 32, "Hrothgar", 0.93)
        )
        self.assertIn("📝 **Rothgr** · heard 3 times", text)
        self.assertIn("Also heard: **Vane**", text)
        self.assertIn("Sounds like **Hrothgar**. Same one, or someone new?", text)
        self.assertIn("2 more names after this one", text)

    def test_without_a_match(self) -> None:
        text = ui.suggestion_text(suggested("Hrothgar"), 1)
        self.assertIn("Is this a name in your game?", text)
        self.assertNotIn("Also heard", text)

    def test_names_are_shown_as_written(self) -> None:
        text = ui.suggestion_text(suggested("*Star*"), 1, match=Match("b" * 32, "*Sun*", 0.9))
        self.assertIn("\\*Star\\*", text)
        self.assertIn("\\*Sun\\*", text)

    def test_its_label_fits_a_phone(self) -> None:
        self.assertEqual(ui.its_label("Hrothgar"), "✅ It's Hrothgar")
        long = ui.its_label("Lord Dagult Neverember the Third")
        self.assertLessEqual(len(long), ui.REVIEW_LABEL_MAX)
        self.assertTrue(long.startswith("✅ It's Lord"))


if __name__ == "__main__":
    unittest.main()
