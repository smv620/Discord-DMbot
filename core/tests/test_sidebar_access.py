"""Who may read the DM sidebar lines (#933, #935)."""

import unittest
from unittest.mock import patch

from dmbot.sidebar import access


class MayRead(unittest.TestCase):
    def test_only_the_campaigns_dms_for_now(self) -> None:
        self.assertTrue(access.may_read(frozenset({7, 8}), 7))
        self.assertFalse(access.may_read(frozenset({7, 8}), 9))
        self.assertFalse(access.may_read(frozenset({7}), None))

    def test_the_owners_other_answer_is_one_line(self) -> None:
        with patch.object(access, "SIDEBAR_READERS", "everyone"):
            self.assertTrue(access.may_read(frozenset({7}), 9))
