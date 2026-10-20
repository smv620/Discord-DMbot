"""A shared recap's token never reaches a log (#2138)."""

from __future__ import annotations

import io
import logging
import unittest

import json

from dmbot.logs import JsonFormatter, TextFormatter, TokenMaskFilter, mask_tokens

TOKEN = "Ab1_-" * 8 + "xyz"  # 43 characters, the shape of a real one


class TheMask(unittest.TestCase):
    def test_a_token_in_a_path_is_hidden_and_the_rest_is_kept(self) -> None:
        for path in (f"/recaps/{TOKEN}", f"/recaps/{TOKEN}/audio/voice", f"/r/{TOKEN}"):
            masked = mask_tokens(f"GET {path}")
            self.assertNotIn(TOKEN, masked)
            self.assertIn("<token>", masked)
        self.assertEqual(mask_tokens("GET /health"), "GET /health")
        self.assertEqual(mask_tokens("/recaps/audio/voice"), "/recaps/audio/voice")  # too short
        self.assertTrue(mask_tokens(f"/recaps/{TOKEN}/audio/voice").endswith("/audio/voice"))

    def test_every_handler_that_has_the_filter_writes_no_token(self) -> None:
        out = io.StringIO()
        handler = logging.StreamHandler(out)
        handler.addFilter(TokenMaskFilter())
        log = logging.getLogger("test.token.mask")
        log.addHandler(handler)
        log.propagate = False
        try:
            log.warning("Request failed: %s %s", "GET", f"/recaps/{TOKEN}")
            try:
                raise ValueError(f"bad /recaps/{TOKEN}")
            except ValueError:
                log.exception("boom")
        finally:
            log.removeHandler(handler)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertIn("/recaps/<token>", out.getvalue())


class TheFormatters(unittest.TestCase):
    def record(self) -> logging.LogRecord:
        try:
            raise ValueError(f"bad /recaps/{TOKEN}")
        except ValueError:
            import sys

            return logging.LogRecord(
                "x", logging.ERROR, __file__, 1, "GET /recaps/%s", (TOKEN,), sys.exc_info(),
                sinfo=f"stack /r/{TOKEN}",
            )  # fmt: skip

    def test_the_json_formatter_masks_message_traceback_and_stack_even_without_the_filter(
        self,
    ) -> None:
        out = JsonFormatter().format(self.record())
        self.assertNotIn(TOKEN, out)
        entry = json.loads(out)
        self.assertIn("/recaps/<token>", entry["msg"])
        self.assertIn("<token>", entry["exc"])
        self.assertIn("/r/<token>", entry["stack"])

    def test_the_text_formatter_masks_everything_too(self) -> None:
        out = TextFormatter().format(self.record())
        self.assertNotIn(TOKEN, out)
        self.assertIn("/recaps/<token>", out)

    def test_a_bad_format_string_does_not_break_the_filter(self) -> None:
        bad = logging.LogRecord("x", logging.INFO, __file__, 1, "%s %s", ("one",), None)
        self.assertTrue(TokenMaskFilter().filter(bad))


if __name__ == "__main__":
    unittest.main()
