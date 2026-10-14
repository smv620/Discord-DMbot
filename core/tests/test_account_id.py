"""The DMbot ID and personal link tokens (#1228): pure, no database."""

from __future__ import annotations

import unittest

from dmbot import account_id as aid
from dmbot import link_tokens as lt


class Ids(unittest.TestCase):
    def test_round_trip(self) -> None:
        for n in (1, 35, 36, 1_234_567_890_123_456_789, 36**12 - 1):
            self.assertEqual(aid.from_dmbot_id(aid.to_dmbot_id(n)), n)

    def test_known_values(self) -> None:
        self.assertEqual(aid.to_dmbot_id(35), "Z")
        self.assertEqual(aid.to_dmbot_id(36), "10")
        self.assertEqual(aid.from_dmbot_id("ZZ"), 36 * 35 + 35)

    def test_a_real_sized_discord_id_fits_in_twelve_characters(self) -> None:
        text = aid.to_dmbot_id(1_500_000_000_000_000_000)
        self.assertLessEqual(len(text), 12)
        self.assertTrue(all(c in aid.DIGITS for c in text))

    def test_the_limit_is_36_to_the_12_minus_1(self) -> None:
        self.assertEqual(aid.to_dmbot_id(36**12 - 1), "Z" * 12)
        with self.assertRaises(ValueError):
            aid.to_dmbot_id(36**12)

    def test_bad_input_is_refused(self) -> None:
        for bad in (0, -5, True):
            with self.assertRaises(ValueError):
                aid.to_dmbot_id(bad)
        for text in ("", "0A", "abc", "A B", "A-1", "Z" * 13, "É", "１２"):
            with self.assertRaises(ValueError, msg=text):
                aid.from_dmbot_id(text)


SECRET = b"a-test-secret-not-a-real-one"
ID = aid.to_dmbot_id(1_234_567_890_123_456_789)
NOW = 1_700_000_000


class Tokens(unittest.TestCase):
    def test_a_fresh_token_checks_out_for_30_days(self) -> None:
        token = lt.make_link_token(ID, NOW, SECRET)
        self.assertTrue(lt.check_link_token(ID, token, NOW, SECRET))
        self.assertTrue(lt.check_link_token(ID, token, NOW + 30 * 86400, SECRET))
        self.assertFalse(lt.check_link_token(ID, token, NOW + 30 * 86400 + 1, SECRET))

    def test_a_token_is_only_for_its_own_id_and_secret(self) -> None:
        token = lt.make_link_token(ID, NOW, SECRET)
        self.assertFalse(lt.check_link_token(aid.to_dmbot_id(42), token, NOW, SECRET))
        self.assertFalse(lt.check_link_token(ID, token, NOW, b"another-secret"))

    def test_tampering_fails(self) -> None:
        token = lt.make_link_token(ID, NOW, SECRET)
        stamp, _, sig = token.partition(".")
        later = f"{int(stamp) + 86400 * 365}.{sig}"  # a longer life, same signature
        flipped = f"{stamp}.{'0' if sig[0] != '0' else '1'}{sig[1:]}"
        for bad in (
            later,
            flipped,
            stamp,
            "",
            ".",
            f"{stamp}.",
            "x.y",
            f"-1.{sig}",
            f"{stamp}.{sig}0",
        ):
            self.assertFalse(lt.check_link_token(ID, bad, NOW, SECRET), bad)

    def test_no_secret_means_no_tokens(self) -> None:
        with self.assertRaises(ValueError):
            lt.make_link_token(ID, NOW, b"")
        self.assertFalse(lt.check_link_token(ID, "1.2", NOW, b""))


if __name__ == "__main__":
    unittest.main()
