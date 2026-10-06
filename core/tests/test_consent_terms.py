"""The consent request's wording is tied to the terms version (#35)."""

import hashlib
import unittest

from dmbot.consent import TERMS_VERSION
from dmbot.consent_dm import request_text

# The request as people see it, under TERMS_VERSION. If this test fails you changed the
# wording: if the change alters what people agree to (who can read it, what's recorded,
# where it's sent), bump consent.TERMS_VERSION so everyone is asked again, then update
# both values here. A pure typo fix may keep the version: update only the fingerprint.
# The "What's new" note for people asked again (consent_dm.RENEWED) explains a change
# rather than adding terms, so it isn't part of the fingerprint.
PINNED_VERSION = 2
PINNED_FINGERPRINT = "5828adf54f32889ddc06b20a689049fb843e651f9ab606519f562277ce391599"


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

    def test_the_request_says_who_can_read_it(self) -> None:
        self.assertIn(
            "Anyone in this server can read",
            request_text("Server", voice=None, dm=None, cloud=False),
        )
