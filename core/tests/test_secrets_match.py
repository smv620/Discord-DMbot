"""The secret-name matcher (#2087): a secret is matched as a whole name only. The owner's own
examples as a table, the two locks (the recap text check and the narration's) agreeing on every
row, and the callers all using the one matcher. Invented names only."""

from __future__ import annotations

import bisect
import time
import unittest
from pathlib import Path

from dmbot import secrets_match as sm
from dmbot.recaps import text as recap_text
from dmbot.recaps.audio import script
from tests import speed

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
    ("Vex", [], "Vexes", True),  # -es on a word ending s, x, z, ch or sh, at any length
    ("Vex", [], "vexing", False),  # -ed and -ing only on words of four letters or more
    ("Vex", [], "Vexed", False),
    ("Bran", [], "brand", False),  # -d only after a final "e"
    ("Ghoule", [], "Ghouled", True),
    ("Rook", [], "Rookes", True),
    ("Ka'zeth", [], "Kaʼzeth spoke", True),  # the modifier apostrophe folds like the others
    ("Vex", [], "Vex's", True),
    ("Vex", [], "the Vexs", True),
    ("Rook", [], "Rooks", True),
    ("Rook", [], "Rooked", True),
    ("Ghost", [], "Ghosted", True),
    ("Ghost", [], "Ghosting", True),
    ("Ghost", [], "Ghosts", True),
    ("Bran", [], "brand", False),
    ("Kin", [], "kind", False),
    ("Lor", [], "lord", False),
    ("Gen", [], "general", False),
    ("Bran", [], "Bran's", True),
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
    # a title that ends a name takes the endings (a leak if it did not)
    ("The Lost General", [], "the lost general's", True),
    ("The Lost General", [], "the lost generals", True),
    ("The Lost General", [], "the lost gen.", True),
    ("The Fallen Saint", [], "the fallen saint's", True),
    ("Mount", [], "mounts", True),
    ("Mount", [], "Mt", False),  # a name that is only a title word is not swapped
    # the name run together, with its article and in any spelling of a title
    ("The White Death", [], "TheWhiteDeath", True),
    ("The White Death", [], "thewhitedeath", True),
    ("The White Death", [], "AWhiteDeath's", True),
    ("Mr. White", [], "MrWhite", True),
    ("Mr. White", [], "Misterwhite", True),
    ("Mr. White", [], "misterwhites", True),
    ("Mr. White", [], "Mrs White", False),
    # a name that is only a short word is looked for with its article
    ("The Ox", [], "the ox", True),
    ("The Ox", [], "an ox", True),
    ("The Ox", [], "the ox's horn", True),
    ("The Ox", [], "the oxen", False),
    ("The Ox", [], "ox", False),
    ("The Ox", [], "a box", False),
    # An article is never glued to a short name: the joins spell everyday words (#2100).
    ("The Ox", [], "theox", False),
    ("The Re", [], "there's", False),
    ("The Me", [], "themed", False),
    ("The D", [], "bread and butter", False),
    ("The Y", [], "they ran", False),
    ("The Se", [], "the theses", False),
    ("An Te", [], "ante", False),
    ("The Ox", [], "box", False),
    ("The Ox", [], "anoxen", False),
    ("The Ox", [], "aox", False),
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


class EverySecretIsFound(unittest.TestCase):
    """Many secrets and no allowed names: each is found alone, whatever its place in the list
    (a cache keyed by a freed object's id once handed one secret another's spans)."""

    def test_the_right_secret_is_found_at_every_place(self) -> None:
        secrets = [f"word{n} word{n}" for n in range(12)]
        for n in range(12):
            for _ in range(20):
                self.assertEqual(
                    sm.secret_rule(f"we met word{n} word{n} today", secrets, []), sm.WHOLE_NAME
                )

    def test_an_unnamed_text_finds_none(self) -> None:
        secrets = [f"word{n} word{n}" for n in range(12)]
        self.assertEqual(sm.secret_rule("we met nobody today", secrets, []), "")


class Speed(unittest.TestCase):
    """The callers run it on the event loop: a long draft with many names must stay quick."""

    def test_three_thousand_words_with_many_names_is_fast(self) -> None:

        words = [f"word{n}" for n in range(3000)]
        words[1500:1503] = ["quiet", "plain", "sentence"]
        text = " ".join(words)
        secrets = [f"Secret Name{n} Holder" for n in range(30)]
        allowed = [f"Allowed Place{n} Hall" for n in range(300)]
        best = min(self.timed(text, secrets, allowed) for _ in range(3))
        self.assertLess(
            best, speed.allowed(0.15)
        )  # (a loaded CI runner took 0.1 s; a slow search would take seconds)
        self.assertFalse(sm.names_a_secret(text, secrets, allowed))
        # And with a secret in it, the allowed names are looked at and the answer is still quick.
        said = text + " Secret Name3 Holder spoke"
        self.assertTrue(sm.names_a_secret(said, secrets, allowed))
        self.assertLess(
            speed.fastest(lambda: sm.names_a_secret(said, secrets, allowed)), speed.allowed(0.2)
        )

    def test_every_match_inside_an_allowed_name_looks_each_name_up_once(self) -> None:
        """The contrived text of #2100: the secret stands only inside the allowed name, thousands
        of times. Each name is searched for once per reading, however many hits there are."""
        from unittest import mock

        text = "Black Gate Keep " * 3000
        readings = len(sm._variants(text))
        with mock.patch.object(sm._Text, "_find", autospec=True, wraps=sm._Text._find) as find:
            started = time.perf_counter()
            self.assertFalse(sm.names_a_secret(text, ["Black Gate"], ["Black Gate Keep"]))
            took = time.perf_counter() - started
        self.assertEqual(find.call_count, 2 * readings)  # the secret and the allowed name, once
        self.assertLess(took, 2)  # (only a bound against a runaway; the count is the proof)
        self.assertTrue(
            sm.names_a_secret(text + " Black Gate", ["Black Gate"], ["Black Gate Keep"])
        )

    def test_a_hit_inside_the_last_of_three_hundred_allowed_names_costs_one_lookup(self) -> None:
        """The hard case: the name that holds every hit is the last one asked. One bisect per hit
        (the old loop asked each allowed name in turn, up to 300 bisects a hit)."""
        from unittest import mock

        allowed = [f"Black Gate Keep{n}" for n in range(300)]
        hits = 200
        text = "Black Gate Keep299 " * hits
        readings = len(sm._variants(text))
        with (
            mock.patch.object(sm._Text, "_find", autospec=True, wraps=sm._Text._find) as find,
            mock.patch("bisect.bisect_left", wraps=bisect.bisect_left) as bisected,
        ):
            started = time.perf_counter()
            self.assertFalse(sm.names_a_secret(text, ["Black Gate"], allowed))
            took = time.perf_counter() - started
        self.assertEqual(find.call_count, 301 * readings)  # each name once per reading
        self.assertEqual(bisected.call_count, hits * readings)  # one per hit
        self.assertLess(took, 2)
        self.assertTrue(sm.names_a_secret(text + " Black Gate", ["Black Gate"], allowed))

    @staticmethod
    def timed(text: str, secrets: list[str], allowed: list[str]) -> float:
        import time

        started = time.perf_counter()
        sm.names_a_secret(text, secrets, allowed)
        return time.perf_counter() - started


class OneMatcher(unittest.TestCase):
    def test_every_caller_uses_the_shared_matcher_and_none_has_a_word_rule(self) -> None:
        """The callers named in #2087 reach `dmbot.secrets_match`, directly or through the recap
        text helpers, and no module keeps a rule of its own for a word of a secret name."""
        root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
        reaches = {
            "recaps/text.py": "secrets_match",
            "recaps/audio/script.py": "secrets_match",
            "recaps/draft.py": "names_a_secret",
            "recaps/beats.py": "names_a_secret",
            "recaps/pipeline.py": "names_a_secret",
            "transcript/cleaner.py": "secrets_match",
            "ui/line_fix.py": "text_names_a_secret",
            "bot.py": "names_a_secret",
            "devtools/recap_run/__main__.py": "names_a_secret",
            "devtools/recap_measure/__main__.py": "names_a_secret",
        }
        for name, needle in reaches.items():
            source = (root / name).read_text(encoding="utf-8")
            self.assertIn(needle, source, name)
        for name in ("recaps/text.py", "recaps/audio/script.py"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertNotIn('"word"', source.replace("'word'", '"word"'), name)
        self.assertNotIn("RUN_MIN", (root / "recaps/audio/script.py").read_text(encoding="utf-8"))
