"""The recap narration's start condition has one text, shared by the worker's pick, its lease and
the campaign move (#2827)."""

from __future__ import annotations

import unittest

from dmbot import audio_due
from dmbot.campaigns import moves
from dmbot.recaps import store


class TheSharedText(unittest.TestCase):
    def test_the_recap_store_and_the_campaign_move_share_one_text(self) -> None:
        self.assertIs(store.AUDIO_DUE_WHERE, audio_due.AUDIO_DUE_WHERE)
        self.assertIs(moves.AUDIO_DUE_WHERE, audio_due.AUDIO_DUE_WHERE)
        self.assertEqual(store.AUDIO_MAX_TRIES, audio_due.AUDIO_MAX_TRIES)
