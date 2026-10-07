"""The payment webhook is the only writer of `entitlements` (#435)."""

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+entitlements\b", re.IGNORECASE)
# The webhook's module (#435 part 3). Account deletion removes the row through
# web_users' ON DELETE CASCADE, never by writing entitlements directly.
ALLOWED = {SRC / "web" / "entitlements_writer.py"}


class OnlyTheWebhookWrites(unittest.TestCase):
    def test_no_other_module_writes_entitlements(self) -> None:
        writers = {
            path
            for path in SRC.rglob("*.py")
            if WRITE.search(path.read_text("utf-8")) and path not in ALLOWED
        }
        self.assertEqual(writers, set(), "only the payment webhook may write entitlements")
