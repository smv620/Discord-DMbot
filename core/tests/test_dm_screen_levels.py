"""How much DMbot says in the DM screen (#504), without Discord or a database."""

import unittest

from dmbot.campaigns.models import DEFAULT_DM_SCREEN_LEVEL, DM_SCREEN_LEVELS
from dmbot.dm_screen.levels import ALERT, FIX_NOTE, NOTICE, QUESTION, allows


class LevelsTest(unittest.TestCase):
    def test_what_each_level_shows(self) -> None:
        shown = {
            level: {kind for kind in (ALERT, QUESTION, FIX_NOTE, NOTICE) if allows(level, kind)}
            for level in DM_SCREEN_LEVELS
        }
        self.assertEqual(shown["quiet"], {ALERT})  # warnings always show
        self.assertEqual(shown["normal"], {ALERT, QUESTION, FIX_NOTE})
        self.assertEqual(shown["chatty"], {ALERT, QUESTION, FIX_NOTE, NOTICE})

    def test_normal_is_the_default_and_unknowns_are_safe(self) -> None:
        self.assertEqual(DEFAULT_DM_SCREEN_LEVEL, "normal")
        self.assertTrue(allows("loud", QUESTION))  # an unknown level counts as normal
        self.assertFalse(allows("loud", NOTICE))
        self.assertFalse(allows("chatty", "something new"))  # never slips past quiet

    def test_plain_words(self) -> None:
        self.assertIn("only what you ask for", DM_SCREEN_LEVELS["quiet"])
        self.assertIn("shows its fixes, one at a time", DM_SCREEN_LEVELS["normal"])
        self.assertIn("fewer misheard names get fixed", DM_SCREEN_LEVELS["quiet"])
        self.assertIn("the same as Normal for now", DM_SCREEN_LEVELS["chatty"])
        self.assertIn("also tells you what it noticed", DM_SCREEN_LEVELS["chatty"])


if __name__ == "__main__":
    unittest.main()
