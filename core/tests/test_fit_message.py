"""`fit_message` keeps a message inside Discord's 2,000 characters without breaking it (#1835)."""

import unittest

from dmbot.discord_limits import MESSAGE_MAX, fit_message


class FitMessageTest(unittest.TestCase):
    def test_fits_unchanged(self) -> None:
        self.assertEqual(fit_message("short"), "short")

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * MESSAGE_MAX
        self.assertEqual(fit_message(text), text)

    def test_cut_at_a_line(self) -> None:
        text = "\n".join(["line " + str(i) * 5 for i in range(500)])
        out = fit_message(text)
        self.assertLessEqual(len(out), MESSAGE_MAX)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(text.startswith(out[:-1]))
        self.assertEqual(text[len(out) - 1], "\n")  # cut sits on a line break

    def test_cut_at_a_space(self) -> None:
        text = "word " * 600
        out = fit_message(text)
        self.assertLessEqual(len(out), MESSAGE_MAX)
        self.assertTrue(out[:-1].endswith("word"))

    def test_a_long_word(self) -> None:
        out = fit_message("x" * 5000)
        self.assertEqual(len(out), MESSAGE_MAX)
        self.assertTrue(out.endswith("…"))

    def test_open_bold_drops_the_last_line(self) -> None:
        text = "first line\n" + "**" + "b" * 100 + "\n" + "tail " * 100
        out = fit_message(text, limit=150)
        self.assertEqual(out.count("**") % 2, 0)
        self.assertLessEqual(len(out), 150)

    def test_open_backtick_on_the_only_line(self) -> None:
        out = fit_message("`" + "a b " * 100, limit=50)
        self.assertEqual(out.count("`") % 2, 0)
        self.assertLessEqual(len(out), 50)

    def test_smaller_limit(self) -> None:
        self.assertLessEqual(len(fit_message("a b " * 100, limit=40)), 40)

    def test_tiny_limits(self) -> None:
        self.assertEqual(fit_message("abc" * 10, limit=0), "")
        self.assertEqual(fit_message("abc" * 10, limit=1), "…")

    def test_only_markers(self) -> None:
        out = fit_message("**" * 100, limit=50)
        self.assertLessEqual(len(out), 50)
        self.assertEqual(out.count("**") % 2, 0)

    def test_open_bold_keeps_earlier_lines(self) -> None:
        out = fit_message("first\n**" + "b" * 100 + "\n" + "tail " * 100, limit=150)
        self.assertEqual(out, "first…")

    def test_one_over_the_limit(self) -> None:
        self.assertLessEqual(len(fit_message("x" * (MESSAGE_MAX + 1))), MESSAGE_MAX)

    def test_an_escaped_marker_is_not_open(self) -> None:
        out = fit_message("x \\` y " * 40, limit=60)
        self.assertGreaterEqual(out.count("\\`"), 3)  # kept: a backslash-escaped one is plain text
