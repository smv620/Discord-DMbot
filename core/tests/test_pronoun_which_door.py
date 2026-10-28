"""The website's pronoun questions keep the same words as the bot's (#2811). Pure."""

from __future__ import annotations

import unittest

from dmbot import schema
from dmbot.memory.pronouns import PRONOUNS


class PronounWhichDoorTests(unittest.TestCase):
    def test_the_door_knows_the_same_pronouns_as_the_bot(self) -> None:
        self.assertEqual(set(schema.PRONOUN_WORDS), set(PRONOUNS))
        self.assertEqual(len(schema.PRONOUN_WORDS), len(set(schema.PRONOUN_WORDS)))

    def test_the_migration_is_registered(self) -> None:
        self.assertIn(("0270_web_pronoun_which", schema.WEB_PRONOUN_WHICH), schema.MIGRATIONS)

    def test_both_doors_are_widened_in_place_so_earlier_changes_to_them_stay(self) -> None:
        """0266 widened the name door at run time ('name' actions, unnamed guards); making it again
        from older text would undo that, so this migration reads each door back as it is."""
        sql = schema.WEB_PRONOUN_WHICH
        self.assertEqual(sql.count("pg_get_functiondef"), 2)
        self.assertNotIn("CREATE OR REPLACE FUNCTION dmbot_web", sql)
        for door in ("dmbot_web_waiting", "dmbot_web_name_action_add"):
            self.assertIn(f"p.proname = '{door}'", sql)

    def test_the_questions_candidates_are_never_null(self) -> None:
        self.assertIn(
            "COALESCE(w.cands, ''[]''::jsonb)".replace("''", "'"), schema.WEB_PRONOUN_WHICH
        )

    def test_the_pronoun_test_trims_before_it_strips_punctuation_as_the_bot_does(self) -> None:
        self.assertIn("btrim(btrim(regexp_replace(q.word", schema._PRONOUN_SQL)


if __name__ == "__main__":
    unittest.main()
