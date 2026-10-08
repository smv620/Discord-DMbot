"""Records of who confirmed the right to use shared material (#252), without a database."""

import re
import unittest

from dmbot.campaigns.models import CONFIRMATION_PURPOSES, fingerprint
from dmbot.schema import SHARED_CONFIRMATIONS


class ConfirmationsTest(unittest.TestCase):
    def test_a_fingerprint_is_sha256_in_lower_case_hex(self) -> None:
        self.assertEqual(
            fingerprint("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        self.assertRegex(fingerprint("Ulfgar ünd Kesh"), r"^[0-9a-f]{64}$")

    def test_the_database_checks_the_same_purposes(self) -> None:
        found = re.search(r"purpose IN \(([^)]*)\)", SHARED_CONFIRMATIONS)
        assert found is not None
        in_sql = tuple(p.strip(" '") for p in found.group(1).split(","))
        self.assertEqual(in_sql, CONFIRMATION_PURPOSES)  # a new purpose needs a migration


if __name__ == "__main__":
    unittest.main()
