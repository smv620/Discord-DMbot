"""The website's pronoun questions keep the same words as the bot's (#2811). Pure."""

from __future__ import annotations

import unittest

from dmbot import schema
from dmbot.memory.pronouns import PRONOUNS


class PronounWhichDoorTests(unittest.TestCase):
    def test_the_door_knows_the_same_pronouns_as_the_bot(self) -> None:
        self.assertEqual(set(schema.PRONOUN_WORDS), set(PRONOUNS))
        self.assertEqual(len(schema.PRONOUN_WORDS), len(set(schema.PRONOUN_WORDS)))

    def test_the_migration_is_last_and_re_creates_both_doors_with_their_settings(self) -> None:
        self.assertEqual(schema.MIGRATIONS[-1][0], "0270_web_pronoun_which")
        sql = schema.WEB_PRONOUN_WHICH
        for door, _args in (
            ("dmbot_web_waiting", "(BIGINT, TEXT)"),
            ("dmbot_web_name_action_add", "(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)"),
        ):
            self.assertEqual(sql.count(f"CREATE OR REPLACE FUNCTION {door}("), 1, door)
            self.assertIn(f"REVOKE ALL ON FUNCTION {door}", sql)
        self.assertEqual(sql.count("SET search_path = pg_catalog"), 2)

    def test_only_a_pronoun_question_may_have_fewer_than_two_names(self) -> None:
        sql = schema.WEB_PRONOUN_WHICH
        self.assertIn(">= CASE WHEN w.pron THEN 0 ELSE 2 END", sql)
        self.assertIn("clean <> w.other_id", sql)  # the own end is no answer


if __name__ == "__main__":
    unittest.main()
