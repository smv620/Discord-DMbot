"""The consent request's wording is tied to the terms version (#35)."""

import hashlib
import unittest

from dmbot.consent import TERMS_VERSION
from dmbot.consent_dm import RENEWED, reminder_text, request_text
from dmbot.ui.logic import HELP_TEXT

# The request as people see it, under TERMS_VERSION. If this test fails you changed the
# wording: if the change alters what people agree to (who can read it, what's recorded,
# where it's sent), bump consent.TERMS_VERSION so everyone is asked again, then update
# both values here. A pure typo fix may keep the version: update only the fingerprint. So
# may a change to a version nobody can have agreed to yet (not deployed).
# The "What's new" note for people asked again (consent_dm.RENEWED) explains a change
# rather than adding terms, so it isn't part of the fingerprint.
PINNED_VERSION = 3  # #52: "with who said it" added before version 3 was live
PINNED_FINGERPRINT = "bcb62efd5252d63d92d31695ae2009b989aa97acbfa1c0241158806fab733b23"


def fingerprint() -> str:
    samples = [
        request_text("Server", voice="Table", dm="Dee", cloud=False),
        request_text("Server", voice=None, dm=None, cloud=True),
    ]
    return hashlib.sha256("\n---\n".join(samples).encode()).hexdigest()


class TermsVersion(unittest.TestCase):
    def test_wording_changes_are_deliberate(self) -> None:
        self.assertEqual(
            (TERMS_VERSION, fingerprint()),
            (PINNED_VERSION, PINNED_FINGERPRINT),
            "The consent request changed. Bump consent.TERMS_VERSION if what people agree "
            "to changed, then update PINNED_VERSION and PINNED_FINGERPRINT.",
        )

    def test_the_request_and_reminder_say_an_ai_reads_the_text(self) -> None:
        # #52: said once for every helper, in the request and in each session's reminder.
        request = request_text("Server", voice=None, dm=None, cloud=False)
        self.assertIn(
            "DMbot's helper reads that text, with who said it, to give your DM notes. For that, "
            "the text goes to an AI company (Anthropic). It isn't used to train their AI.",
            request,
        )
        reminder = reminder_text("Server", "Table", 1_700_000_000)
        self.assertIn(
            "An AI company (Anthropic) reads the text, with who said it, to give your DM notes",
            reminder,
        )
        # The note for people asked again, and /dmbot help, say the same (#52).
        self.assertIn("with who said it", RENEWED)
        self.assertIn("with who said it", HELP_TEXT)
        self.assertIn("isn't used to train their AI", HELP_TEXT)
        self.assertIn("isn't used to train their AI", reminder)

    def test_the_request_says_who_can_read_it(self) -> None:
        self.assertIn(
            "Anyone in this server can read",
            request_text("Server", voice=None, dm=None, cloud=False),
        )
