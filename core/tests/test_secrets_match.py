"""The secret-name matcher (#2087): a secret is matched as a whole name only. The owner's own
examples as a table, the two locks (the recap text check and the narration's) agreeing on every
row, and the callers all using the one matcher. Invented names only."""

from __future__ import annotations

import unittest
from pathlib import Path

from dmbot import secrets_match as sm
from dmbot.recaps import text as recap_text
from dmbot.recaps.audio import script

# (secret, allowed names, text, blocked?)
ROWS = [
    # "The White Death"
    ("The White Death", [], "a white death", True),
    ("The White Death", [], "the white death", True),
    ("The White Death", [], "The White Death's", True),
    ("The White Death", [], "WhiteDeath", True),
    ("The White Death", [], "the white deaths", True),
    ("The White Death", [], "A White Death came", True),
    ("The White Death", [], "the white lady", False),
    ("The White Death", [], "an icy death", False),
    ("The White Death", [], "death", False),
    ("The White Death", [], "White rose; death followed", False),
    # "Mr. White"
    ("Mr. White", [], "Mr. White's", True),
    ("Mr. White", [], "Mister White", True),
    ("Mr. White", [], "mr white", True),
    ("Mr. White", [], "Mr White", True),
    ("Mr. White", [], "Mr.White waits", True),
    ("Mr. White", [], "Mr. Weaton", False),
    ("Mr. White", [], "white", False),
    ("Mr. White", [], "Mr. Black", False),
    ("Mister White", [], "Mr. White", True),  # and the other way round
    ("Dr. Vane", [], "Doctor Vane spoke", True),
    ("Capt. Rook", [], "Captain Rooks", True),
    ("Saint Orla", [], "St. Orla's", True),
    ("Mount Kerrin", [], "Mt Kerrin", True),
    ("Prof. Aldous", [], "professor aldous", True),
    # "Arcane Brotherhood"
    ("Arcane Brotherhood", [], "the arcane brotherhood", True),
    ("Arcane Brotherhood", [], "arcane's brotherhood", True),
    ("Arcane Brotherhood", [], "arcane brotherhood's", True),
    ("Arcane Brotherhood", [], "the brotherhood", False),
    ("Arcane Brotherhood", [], "arcane", False),
    ("Arcane Brotherhood", ["Arcane Brotherhood Hall"], "Arcane Brotherhood Hall rose", False),
    ("Arcane Brotherhood", ["Arcane Brotherhood Hall"], "the Arcane Brotherhood met", True),
    # one word
    ("Wit", [], "wits", True),
    ("Wit", [], "Wit", True),
    ("Wit", [], "Witness", False),
    ("Vex", [], "Vexes", True),
    ("Vex", [], "vexing", True),
    ("Vex", [], "Vexation grew", False),
    ("Vex", [], "VEX!", True),
    ("Vex", [], "Vex-the-Younger", True),
    ("Rae", [], "Rae-Li", True),
    ("Rae", [], "A brave rider crossed the raeburn road", False),
    ("Ox", [], "an ox", False),  # under three letters: not looked for
    # spellings
    ("Zoë", [], "Then Zoe smiled", True),
    ("Ka'zeth", [], "Kazeth spoke", True),
    ("Dun-Varra", [], "Dun Varra's keep", True),
    ("Dun-Varra", [], "Dun-Varra rose", True),
    ("Dun-Varra", [], "Then Varra spoke", False),
    ("Silas Vane", [], "SilasVane rode on", True),
    ("Silas Vane", [], "Silas rode; Vane waited", False),
    ("Silas Vane", ["Silas Brook"], "Silas laughed", False),
    ("Rae", ["Rae Brook"], "Rae Brook laughed", False),
    ("✶✶", [], "a ✶✶ appeared", True),
    # a title in the secret but not the text, and the reverse
    ("White", [], "Mr. White", True),
    ("Vex-gate", [], "the Vex gate fell", True),
]


class TheOwnersExamples(unittest.TestCase):
    def test_each_row_is_blocked_or_not(self) -> None:
        for secret, allowed, text, blocked in ROWS:
            with self.subTest(secret=secret, text=text):
                self.assertEqual(sm.names_a_secret(text, [secret], allowed), blocked)

    def test_the_two_locks_agree_on_every_row(self) -> None:
        for secret, allowed, text, blocked in ROWS:
            with self.subTest(secret=secret, text=text):
                self.assertEqual(
                    bool(recap_text.secret_rule(text, [secret], allowed)),
                    bool(script.secret_rule(text, [secret], allowed)),
                )
                refused = ""
                try:
                    script.build(
                        "T", None, f"《Calm》 {text}", secret_names=[secret], allowed_names=allowed
                    )
                except script.SecretInScript as exc:
                    refused = exc.rule
                self.assertEqual(bool(refused), blocked)

    def test_the_rule_is_a_fixed_word_and_never_the_name(self) -> None:
        for secret, _allowed, text, blocked in ROWS:
            rule = sm.secret_rule(text, [secret], [])
            if blocked and rule:
                self.assertIn(rule, (sm.WHOLE_NAME, sm.RUN_TOGETHER))
                self.assertNotIn(secret.casefold(), rule)

    def test_several_secrets_glued_into_one_word_still_block_but_a_lone_one_is_not_double_counted(
        self,
    ) -> None:
        self.assertEqual(sm.secret_rule("RaeAegis stood", ["Rae", "Aegis"], []), sm.RUN_TOGETHER)
        # "Rae" inside an allowed longer name is that name: nothing else glued, nothing blocked
        self.assertEqual(sm.secret_rule("Rae Brook", ["Rae", "Aegis"], ["Rae Brook"]), "")

    def test_a_secret_only_in_symbols_is_looked_for_as_written(self) -> None:
        self.assertTrue(sm.names_a_secret("a ✶✶ appeared", ["✶✶"], []))
        self.assertFalse(sm.names_a_secret("a star appeared", ["✶✶"], []))

    def test_no_names_means_nothing_is_blocked(self) -> None:
        self.assertFalse(sm.names_a_secret("anything at all", [], []))
        self.assertFalse(sm.names_a_secret("", ["Vex"], []))


class OneMatcher(unittest.TestCase):
    def test_no_caller_has_a_word_rule_of_its_own(self) -> None:
        """The recap text helpers and the narration lock only call the shared matcher."""
        root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
        for name in ("recaps/text.py", "recaps/audio/script.py"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertIn("secrets_match", source, name)
            self.assertNotIn('"word"', source.replace("'word'", '"word"'), name)
