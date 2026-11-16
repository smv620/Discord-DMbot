"""Reads at once on one server (#4551): the website's lore reads take one campaign each, up to the
server's cap; Discord's button still takes the whole server; the day's allowance is shared."""

from __future__ import annotations

import time
import unittest

from dmbot.ui import name_lists as nl

G = 1
NOW = time.time()


class ReadsAtOnce(unittest.TestCase):
    def setUp(self) -> None:
        nl._ai_busy.clear()
        nl._ai_reads.clear()

    tearDown = setUp

    def test_two_campaigns_read_together_up_to_the_cap(self) -> None:
        self.assertIsNone(nl.start_ai_read(G, NOW, "a", 2))
        self.assertIsNone(nl.start_ai_read(G, NOW, "b", 2))
        self.assertEqual(nl.start_ai_read(G, NOW, "c", 2), nl.BUSY)  # the server's cap

    def test_one_campaign_never_reads_twice(self) -> None:
        self.assertIsNone(nl.start_ai_read(G, NOW, "a", 5))
        self.assertEqual(nl.start_ai_read(G, NOW, "a", 5), nl.BUSY)

    def test_discords_button_still_takes_the_whole_server(self) -> None:
        self.assertIsNone(nl.start_ai_read(G, NOW))
        self.assertEqual(nl.start_ai_read(G, NOW), nl.BUSY)  # (the default allows one)

    def test_a_website_read_running_keeps_discords_button_waiting(self) -> None:
        self.assertIsNone(nl.start_ai_read(G, NOW, "a", 2))
        self.assertEqual(nl.start_ai_read(G, NOW), nl.BUSY)

    def test_ending_one_read_leaves_the_others_marked(self) -> None:
        nl.start_ai_read(G, NOW, "a", 2)
        nl.start_ai_read(G, NOW, "b", 2)
        nl.end_ai_read(G, "a")
        self.assertEqual(nl.start_ai_read(G, NOW, "b", 2), nl.BUSY)  # b still reads
        self.assertIsNone(nl.start_ai_read(G, NOW, "c", 2))  # a's place is free
        nl.end_ai_read(G, "b")
        nl.end_ai_read(G, "c")
        self.assertEqual(nl._ai_busy, {})

    def test_the_days_allowance_is_shared_and_other_servers_are_apart(self) -> None:
        for n in range(nl.AI_READS_PER_DAY):
            self.assertIsNone(nl.start_ai_read(G, NOW, f"c{n}", 2))
            nl.end_ai_read(G, f"c{n}")
        self.assertEqual(nl.start_ai_read(G, NOW, "x", 2), nl.LIMIT)
        self.assertIsNone(nl.start_ai_read(2, NOW, "x", 2))

    def test_a_link_is_one_at_a_time_for_each_campaign_and_for_discord(self) -> None:
        nl._link_busy.add((G, "a"))
        self.assertTrue(nl.link_is_busy(G, "a"))
        self.assertFalse(nl.link_is_busy(G, "b"))
        self.assertFalse(nl.link_is_busy(G))
        nl._link_busy.clear()

    def test_a_server_at_the_set_limit_gets_limit_and_a_higher_limit_lets_more_in(self) -> None:
        """(#4582: the day's limit is a setting, shared by Discord's button and the website)"""
        for n in range(3):
            self.assertIsNone(nl.start_ai_read(G, NOW, f"c{n}", 5, 3))
            nl.end_ai_read(G, f"c{n}")
        self.assertEqual(nl.start_ai_read(G, NOW, "x", 5, 3), nl.LIMIT)
        self.assertIsNone(nl.start_ai_read(G, NOW, "x", 5, 60))  # (a higher limit: room again)
