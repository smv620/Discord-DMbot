"""The keyed hash kept of a Discord ID after an account is deleted (#4145). Pure: no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dmbot import person_key
from dmbot.config import Settings
from dmbot.person_key import PersonKeyMissing, PersonKeys

SECRET = "s" * 40
ALICE, BOB = 7001, 7002


class TheKeys(unittest.TestCase):
    def test_the_same_id_gives_the_same_key_and_it_is_64_lowercase_hex(self) -> None:
        keys = PersonKeys(SECRET)
        self.assertEqual(keys.of(ALICE), keys.of(ALICE))
        self.assertTrue(person_key.valid(keys.of(ALICE)))

    def test_another_id_or_another_secret_gives_another_key(self) -> None:
        keys = PersonKeys(SECRET)
        self.assertNotEqual(keys.of(ALICE), keys.of(BOB))
        self.assertNotEqual(keys.of(ALICE), PersonKeys("t" * 40).of(ALICE))

    def test_the_key_is_not_the_plain_hash_of_the_id(self) -> None:
        import hashlib

        plain = hashlib.sha256(str(ALICE).encode()).hexdigest()
        self.assertNotEqual(
            PersonKeys(SECRET).of(ALICE), plain
        )  # (guessable IDs: it must be keyed)

    def test_a_value_that_is_not_a_discord_id_is_refused(self) -> None:
        keys = PersonKeys(SECRET)
        for bad in (0, -5, 2**63, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                keys.of(bad)

    def test_a_missing_or_short_secret_fails_closed(self) -> None:
        for secret in ("", "short", "x" * 31):
            with self.subTest(secret=secret), self.assertRaises(PersonKeyMissing):
                PersonKeys(secret)

    def test_the_secret_is_never_in_the_error_or_the_settings_repr(self) -> None:
        with self.assertRaises(PersonKeyMissing) as raised:
            PersonKeys("tooshort")
        self.assertNotIn("tooshort", str(raised.exception))
        self.assertNotIn(
            SECRET, repr(Settings(discord_token="t", ears_secret="e", person_key=SECRET))
        )


class TheShape(unittest.TestCase):
    def test_only_64_lowercase_hex_characters_are_a_key(self) -> None:
        good = "a" * 64
        self.assertTrue(person_key.valid(good))
        for bad in ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 63, None, 7, b"a" * 64):
            with self.subTest(bad=bad):
                self.assertFalse(person_key.valid(bad))


class FromSettings(unittest.TestCase):
    def test_built_from_the_settings_and_refused_when_unset(self) -> None:
        keys = person_key.from_settings(SimpleNamespace(person_key=SECRET))
        self.assertEqual(keys.of(ALICE), PersonKeys(SECRET).of(ALICE))
        with self.assertRaises(PersonKeyMissing):
            person_key.from_settings(SimpleNamespace(person_key=""))
        with self.assertRaises(PersonKeyMissing):
            person_key.from_settings(SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
