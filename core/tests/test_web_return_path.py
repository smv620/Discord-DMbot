"""The sign-in return address (#2301): plain account pages are kept, anything else is dropped."""

import unittest

from dmbot.web.return_path import MAX_LENGTH, safe_return_path


class SafeReturnPath(unittest.TestCase):
    def test_account_pages_are_kept(self) -> None:
        for path in (
            "/account",
            "/account/",
            "/account/import/100.cmp1",
            "/account/recaps/123456789012345678.cmp-1",
            "/account/names/100.cmp1/",
        ):
            self.assertEqual(safe_return_path(path), path)

    def test_nothing_that_leaves_the_account_pages_is_kept(self) -> None:
        for path in (
            None,
            "",
            "account",
            "/",
            "/admin",
            "/accounts",
            "//evil.example",
            "/account//evil.example",
            "/account/../admin",
            "/account/./x",
            "/account\\evil",
            "/account/\\evil.com",
            "https://evil.example/account",
            "/account?x=1",
            "/account#frag",
            "/account/a b",
            "/account/%2e%2e/admin",
            "/account/x\n",
        ):
            self.assertIsNone(safe_return_path(path), path)

    def test_a_very_long_path_is_dropped(self) -> None:
        self.assertIsNone(safe_return_path("/account/" + "a" * MAX_LENGTH))


if __name__ == "__main__":
    unittest.main()
