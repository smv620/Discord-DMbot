"""The note on something that happened to a player character (#2562) is not a detail (#3624): it is
stored run on to the claim's details, and every reader takes it off again. Pure. Invented names."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from dmbot.memory.claims import claim_of, feeling_of
from dmbot.memory.voice import EFFECT_NOTE, split_effect_note
from dmbot.ui.claims_review import page_text, statement
from dmbot.web.relationships import _can_be_secret, _claim_statement
from tests.test_voice import ONTO

NOTE = EFFECT_NOTE.format(name="Belleros")


def row(details: str) -> dict[str, Any]:
    return {
        "id": "c1", "subject": "Belleros", "relationship": "knows", "object": "Brewner",
        "details": details, "how": "dm_said", "lines": ["1"], "speaker_ids": [7],
        "source": "extractor", "prompt_version": "p", "model": "m", "status": "proposed",
        "reason": "", "session_started_at": 1,
        "created_at": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
        "decided_at": None, "fact_id": None,
    }  # fmt: skip


class TheSplit(unittest.TestCase):
    def test_the_note_comes_off_the_details(self) -> None:
        self.assertEqual(split_effect_note(f"friendly {NOTE}"), ("friendly", NOTE))
        self.assertEqual(split_effect_note(NOTE), ("", NOTE))
        self.assertEqual(split_effect_note("friendly"), ("friendly", ""))
        long = EFFECT_NOTE.format(name="Mira Voss")
        self.assertEqual(split_effect_note(f"a (b) {long}"), ("a (b)", long))

    def test_names_with_apostrophes_brackets_and_dots_come_off_too(self) -> None:
        for name in ("O'Neil", "O\u2019Neil", "A.B", "Mira (Voss)", "Two\nLines"):
            with self.subTest(name):
                note = EFFECT_NOTE.format(name=name)
                self.assertEqual(split_effect_note(f"friendly {note}"), ("friendly", note))

    def test_other_words_that_only_start_alike_are_left_alone(self) -> None:
        said = "Told by the DM as something that happened"
        self.assertEqual(split_effect_note(said), (said, ""))


class OnTheDmScreen(unittest.TestCase):
    def test_the_details_and_the_note_are_apart_on_a_stored_claim(self) -> None:
        claim = claim_of(row(f"friendly {NOTE}"))
        self.assertEqual((claim.details, claim.note), ("friendly", NOTE))

    def test_the_question_does_not_run_the_note_into_the_details(self) -> None:
        claim = claim_of(row(f"friendly {NOTE}"))
        self.assertEqual(statement(claim), "**Belleros** knows **Brewner** (friendly)")
        text = page_text([claim], 1)
        self.assertNotIn("(friendly Told", text)
        self.assertIn(f"_{NOTE}_", text.replace("\\", ""))  # (its own line)

    def test_a_feeling_word_with_the_note_after_it_is_still_a_feeling(self) -> None:
        claim = claim_of(row(f"friendly {NOTE}"))
        self.assertIsNotNone(feeling_of(ONTO, claim.relationship, claim.details))


class OnTheWebsite(unittest.TestCase):
    def test_the_waiting_page_line_has_no_note(self) -> None:
        self.assertEqual(
            _claim_statement(row(f"friendly {NOTE}")), "Belleros knows Brewner (friendly)"
        )
        self.assertEqual(_claim_statement(row(NOTE)), "Belleros knows Brewner")

    def test_the_secret_offer_is_still_made_with_the_note_on(self) -> None:
        self.assertTrue(_can_be_secret(row(f"friendly {NOTE}")))
