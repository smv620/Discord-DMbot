"""The website's consent promises follow the bot's terms version (#1882).

/players and Help's player section show the list in web/src/content/consent-promises.ts, which
says what the consent request promises. When consent.TERMS_VERSION goes up, whoever changed the
terms must read that list and the two pages that show it, then bump the number in the file's
TERMS_VERSION comment, so the site can never keep promising what an older request said.
"""

import re
import unittest
from pathlib import Path

from dmbot.consent import TERMS_VERSION

PROMISES = Path(__file__).resolve().parents[2] / "web" / "src" / "content" / "consent-promises.ts"


class ConsentPromisesFollowTheTerms(unittest.TestCase):
    def test_the_site_file_names_the_current_terms_version(self) -> None:
        match = re.search(r"^// TERMS_VERSION: (\d+)$", PROMISES.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(match, f"{PROMISES} has no '// TERMS_VERSION: N' line")
        assert match is not None  # for mypy
        self.assertEqual(
            int(match.group(1)),
            TERMS_VERSION,
            "consent.TERMS_VERSION changed. Read web/src/content/consent-promises.ts and the "
            "/players and Help pages that show it; fix what no longer matches the consent "
            "request, then set the '// TERMS_VERSION:' line in that file to the new number.",
        )


if __name__ == "__main__":
    unittest.main()
