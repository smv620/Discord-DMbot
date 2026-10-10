"""The consent request's wording is tied to the terms version (#35)."""

import hashlib
import unittest

from dmbot.consent import TERMS_VERSION
from dmbot.consent_dm import RENEWED, reminder_text, request_text
from dmbot.consent_words import AGE_LINE, CONSENT_LABEL, MIN_AGE, UNDER_LINE
from dmbot.ui.logic import HELP_TEXT

# The request as people see it, under TERMS_VERSION. If this test fails you changed the
# wording: if the change alters what people agree to (who can read it, what's recorded,
# where it's sent), bump consent.TERMS_VERSION so everyone is asked again, then update
# both values here. A pure typo fix may keep the version: update only the fingerprint. So
# may a change to a version nobody can have agreed to yet (not deployed).
# The "What's new" note for people asked again (consent_dm.RENEWED) explains a change
# rather than adding terms, so it isn't part of the fingerprint.
PINNED_VERSION = 4  # #1018: "You must be 16 or older to be recorded." and the button says so
PINNED_FINGERPRINT = "297682882bac77d772afdb77d6f46169d1f1d67864d82037366227dcc3f71f69"


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
        # /dmbot help says the same (#52).
        self.assertIn("with who said it", HELP_TEXT)
        self.assertIn("isn't used to train their AI", HELP_TEXT)
        self.assertIn("isn't used to train their AI", reminder)

    def test_the_request_and_the_button_ask_for_the_minimum_age(self) -> None:
        # Owner, 2026-10-10 (#1018): DMbot's minimum age is 16, and pressing the button
        # confirms it. Nothing about age is stored.
        for text in (
            request_text("Server", voice="Table", dm="Dee", cloud=False),  # the private message
            request_text("Server", voice=None, dm=None, cloud=True, renewed=True),  # /consent give
        ):
            self.assertIn("You must be 16 or older to be recorded.", text)
            # A younger player is told what to do and that they can still play.
            self.assertIn("Younger than 16? Press **No thanks**. You can still play.", text)
            self.assertIn(f"Press **{CONSENT_LABEL}** and DMbot records what you say", text)
        self.assertIn("16", CONSENT_LABEL)
        self.assertLessEqual(len(CONSENT_LABEL), 80)  # Discord's limit for a button
        self.assertIn(f"**{CONSENT_LABEL}**", HELP_TEXT)
        self.assertIn("Only people 16 or older are recorded", HELP_TEXT)

    def test_the_age_is_written_once(self) -> None:
        # Every line that names the age is built from MIN_AGE, so a later change can't leave
        # one behind (the Supervisor's note on #1023).
        for line in (AGE_LINE, UNDER_LINE, CONSENT_LABEL, RENEWED):
            self.assertIn(str(MIN_AGE), line)
        self.assertEqual(AGE_LINE, f"You must be {MIN_AGE} or older to be recorded.")
        self.assertEqual(
            UNDER_LINE, f"Younger than {MIN_AGE}? Press **No thanks**. You can still play."
        )

    def test_the_note_for_people_asked_again_says_why(self) -> None:
        self.assertIn("16 or older", RENEWED)
        self.assertIn("asking you again", RENEWED)
        self.assertIn("won't record you until you press the button", RENEWED)
        self.assertIn("Younger than 16? Press **No thanks** and keep playing.", RENEWED)

    def test_no_age_or_birthdate_is_kept_anywhere(self) -> None:
        # Pressing the button is the confirmation (#1018): no column for it.
        from dmbot import schema

        create = next(
            m for name, m in schema.MIGRATIONS if name == "0001_initial"
        )  # the consent table is made here
        block = create[create.index("CREATE TABLE consent") :].split(");", 1)[0].casefold()
        for word in ("age", "birth", "dob"):
            self.assertNotRegex(block, rf"\b{word}\w*\b")

    def test_nobody_is_asked_for_an_age_or_a_birthdate(self) -> None:
        text = request_text("Server", voice=None, dm=None, cloud=False).casefold()
        for word in ("birth", "how old are you", "enter your age", "date of"):
            self.assertNotIn(word, text)

    def test_the_request_says_who_can_read_it(self) -> None:
        self.assertIn(
            "Anyone in this server can read",
            request_text("Server", voice=None, dm=None, cloud=False),
        )
