"""The audio's second secret lock agrees with the recap text's own check (#2050): a short secret
name inside ordinary words, or run across a word gap, no longer stops the voice, while a real
name in any form still does. Invented names only."""

from __future__ import annotations

import unittest
from collections.abc import Collection
from typing import ClassVar

from dmbot.recaps.audio import script
from dmbot.recaps.text import names_a_secret, secret_rule

RAE = "Rae"  # a short secret: three letters
VEX = "Vex"
DUN = "Dun-Varra"
SILAS = "Silas Vane"


def refused(text: str, secret: Collection[str], allowed: Collection[str] = ()) -> str:
    """The rule that stops the voice for this text, or "" when it is built."""
    try:
        script.build("T", None, f"《Calm》 {text}", secret_names=secret, allowed_names=allowed)
    except script.SecretInScript as exc:
        return exc.rule
    return ""


class ShortSecretsInOrdinaryWords(unittest.TestCase):
    def test_a_three_letter_secret_inside_ordinary_words_does_not_block(self) -> None:
        for text in (
            "They stood there. Aegis shone on the wall.",  # "there aegis" runs through "rae"
            "A brave rider crossed the raeburn road.",
            "The caravan paused near the vexation bridge.",  # "vex" begins an ordinary word
        ):
            self.assertEqual(refused(text, [RAE, VEX]), "", text)

    def test_the_name_as_a_word_its_possessive_and_plural_do_block(self) -> None:
        self.assertEqual(refused("Rae walked in.", [RAE]), "whole-name")
        self.assertEqual(refused("It was Vex's blade.", [VEX]), "whole-name")
        self.assertEqual(refused("The Vexs arrived.", [VEX]), "whole-name")  # (s on any word)
        self.assertEqual(refused("The Vexes arrived.", [VEX]), "whole-name")  # -es after an x

    def test_hyphen_and_spaced_spellings_block(self) -> None:
        self.assertEqual(refused("Dun-Varra rose.", [DUN]), "whole-name")
        self.assertEqual(refused("Dun Varra's keep.", [DUN]), "whole-name")
        self.assertEqual(refused("Then Varra spoke.", [DUN]), "")  # one word of it: not the name

    def test_a_long_secret_run_together_still_blocks_but_only_at_a_word_start(self) -> None:
        self.assertEqual(refused("SilasVane rode on.", [SILAS]), "run-together")
        self.assertEqual(refused("Her friend, a smith named silas-vane.", [SILAS]), "whole-name")
        # inside another word, or across a gap that is not the name: not the name
        self.assertEqual(refused("The pinsilas vane turned.", [SILAS]), "")
        self.assertEqual(refused("The pinsilasvane turned.", [SILAS]), "")

    def test_a_secret_word_a_confirmed_name_also_uses_is_not_blocked(self) -> None:
        # ("Silas" alone is not the secret name "Silas Vane" at all, #2087)
        self.assertEqual(refused("Silas laughed.", [SILAS], allowed=["Silas Brook"]), "")
        self.assertEqual(
            refused("Silas Vane laughed.", [SILAS], allowed=["Silas Brook"]), "whole-name"
        )


class SpellingsOfShortNames(unittest.TestCase):
    """Short names too are caught however the narrator spells them (the old lock caught these)."""

    def test_accents_apostrophes_and_hyphens_are_ignored(self) -> None:
        self.assertTrue(refused("Then Zoe smiled.", ["Zoë"]))
        self.assertTrue(refused("Kael drew his sword.", ["Ka'el"]))
        self.assertTrue(refused("Vexa laughed.", ["Vex'a"]))
        self.assertTrue(refused("RaeLi bowed.", ["Rae-Li"]))
        self.assertTrue(refused("Severin Voss left.", ["Séverin Voss"]))

    def test_short_secrets_glued_together_block(self) -> None:
        self.assertEqual(refused("RaeAegis stood alone.", ["Rae", "Aegis"]), "run-together")
        self.assertEqual(refused("There Aegis stood.", ["Rae", "Aegis"]), "whole-name")

    def test_a_short_secret_joined_to_other_letters_is_the_accepted_gap(self) -> None:
        self.assertEqual(refused("The vexation road.", [VEX]), "")
        self.assertEqual(refused("The vexing road.", [VEX]), "")  # (endings need five letters)


class TheTwoLocksAgree(unittest.TestCase):
    """Every text the recap check passes, with no secret word in it, is built by the audio."""

    SECRETS: ClassVar[list[str]] = [RAE, VEX, DUN, SILAS, "Ka'zeth", "Oru"]
    TEXTS: ClassVar[tuple[str, ...]] = (
        "The party reached the stronghold at dusk, and the bridge held.",
        "There aegis, a rare shield, hung above the great door.",
        "A story told over hot soup near a raven's nest, by an overgrown road.",
        "Brave Mira crossed the vexing river; Oskar followed, calm.",
        "Fierce winds ran Southwest, and a voracious storm rose.",
    )

    def test_a_text_the_recap_check_passes_is_built_by_the_audio(self) -> None:
        for text in self.TEXTS:
            if not names_a_secret(text, self.SECRETS, []):
                self.assertEqual(refused(text, self.SECRETS), "", text)

    def test_a_text_the_recap_check_refuses_is_refused_by_the_audio_with_its_rule(self) -> None:
        for text in ("Vex's axe.", "Dun-Varra smiled.", "Then Dun Varra spoke."):
            rule = secret_rule(text, self.SECRETS, [])
            self.assertTrue(rule, text)
            self.assertEqual(refused(text, self.SECRETS), rule, text)

    def test_the_rule_is_a_fixed_word_and_never_the_name(self) -> None:
        for text, secret in (("Rae spoke.", [RAE]), ("SilasVane rode.", [SILAS])):
            rule = refused(text, secret)
            self.assertIn(rule, ("whole-name", "run-together"))
            try:
                script.build("T", None, text, secret_names=secret)
            except script.SecretInScript as exc:
                self.assertNotIn(secret[0].casefold(), str(exc).casefold())


if __name__ == "__main__":
    unittest.main()


class TheAllowedNames(unittest.TestCase):
    def test_only_confirmed_names_that_are_not_secret_or_a_person_are_allowed(self) -> None:
        from dmbot.recaps.audio import names
        from dmbot.recaps.audio.worker import AudioInputs

        spoken = [
            names.SpokenName("Silas Brook"),
            names.SpokenName("Vex", secret=True),
            names.SpokenName("Maybe", confirmed=False),
            names.SpokenName("Rae", person=True),  # a player's own name opens no secret's word
        ]
        self.assertEqual(AudioInputs([], spoken).allowed_names, ["Silas Brook"])
