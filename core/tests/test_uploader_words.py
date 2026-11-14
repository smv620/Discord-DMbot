"""The uploader's own consent words (#3305): each fact the owner set is in them and the words that
must not be there are not; they are held back by a switch (the Discord words stay until it is on);
once on, the page gets its own version, a stale version is refused, and every tick records the
uploader's words version, not the Discord terms. Invented names."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dmbot.consent import TERMS_VERSION
from dmbot.transcript import uploader_words as uw
from dmbot.web import imports


class TheWords(unittest.TestCase):
    def test_every_fact_the_owner_set_is_there(self) -> None:
        text = uw.TEXT
        for needle in (
            "for each person you tick, you are confirming that they agreed",
            "Anyone in this campaign's server can read and download that text",
            "An AI company (Anthropic) reads the text",
            "It is not used to train their AI",
            "16 or older",
            "13 to 15",
            "you must check with their parent or guardian",
            "tick only if the parent or guardian agreed",
            "Never tick for anyone under 13",
            "their lines stay until they ask for them to be removed",
            "on Discord with Delete my transcripts",
            "if they are not on Discord, through the DM",
        ):
            self.assertIn(needle, text)

    def test_the_words_that_must_not_be_there_are_not(self) -> None:
        text = uw.TEXT.lower()
        for banned in (
            "press",
            "asks again",
            "record you",
            "18",
            "stop recording me",
            "even if they change",
        ):
            self.assertNotIn(banned, text)

    def test_the_tick_says_who_checked_with_whom(self) -> None:
        shown = uw.TICK.format(name="Old Greg")
        self.assertEqual(
            shown,
            "I confirm Old Greg agreed to this, or, if they are 13 to 15, that I checked with "
            "their parent or guardian and they agreed.",
        )

    def test_the_words_have_their_own_number_space(self) -> None:
        from dmbot.consent import IMAGE_TERMS_VERSION

        # a recorded version must never be mistaken for a Discord or image terms version
        self.assertGreaterEqual(uw.IMPORT_WORDS_VERSION, 100)
        self.assertNotIn(uw.IMPORT_WORDS_VERSION, {TERMS_VERSION, IMAGE_TERMS_VERSION})
        self.assertGreater(uw.IMPORT_WORDS_VERSION, TERMS_VERSION + 1)  # (the held v7 too)

    def test_it_is_off_until_turned_on_and_then_has_its_own_version(self) -> None:
        self.assertFalse(uw.UPLOADER_WORDS_LIVE)
        self.assertEqual(uw.version(), TERMS_VERSION)
        with patch.object(uw, "UPLOADER_WORDS_LIVE", True):
            self.assertEqual(uw.version(), uw.IMPORT_WORDS_VERSION)

    def test_the_page_gets_the_discord_words_while_off_and_these_once_on(self) -> None:
        off = imports.consent_words(cloud=False, company=None)
        self.assertEqual(off["version"], TERMS_VERSION)
        self.assertNotIn("tick", off)
        with patch.object(uw, "UPLOADER_WORDS_LIVE", True):
            on = imports.consent_words(cloud=False, company=None)
        self.assertEqual(
            (on["version"], on["text"], on["tick"]), (uw.IMPORT_WORDS_VERSION, uw.TEXT, uw.TICK)
        )
