import unittest

from dmbot.sharding import ShardConfigError, ShardSettings, parse_shards, shard_for


class ParseShards(unittest.TestCase):
    def test_defaults_to_one_shard(self) -> None:
        self.assertEqual(parse_shards("", ""), ShardSettings(1, (0,)))

    def test_count_alone_means_all_shards(self) -> None:
        self.assertEqual(parse_shards("3", ""), ShardSettings(3, (0, 1, 2)))

    def test_explicit_ids_are_sorted(self) -> None:
        self.assertEqual(parse_shards("4", " 3, 1 "), ShardSettings(4, (1, 3)))

    def test_rejects_bad_settings(self) -> None:
        cases = {
            ("zero", ""): "whole number",
            ("0", ""): "between 1 and",
            ("2", "2"): "SHARD_COUNT is 2",
            ("2", "1,1"): "twice",
            ("2", "a"): "separated by commas",
            ("2", "-1"): "separated by commas",
            ("²", ""): "whole number",
        }
        for (count, ids), message in cases.items():
            with (
                self.subTest(count=count, ids=ids),
                self.assertRaisesRegex(ShardConfigError, message),
            ):
                parse_shards(count, ids)


class ShardRule(unittest.TestCase):
    def test_matches_discord(self) -> None:
        # Same vectors as ears/test/shards.test.ts.
        self.assertEqual(shard_for(81384788765712384, 1), 0)
        self.assertEqual(shard_for(81384788765712384, 2), 0)
        self.assertEqual(shard_for(81384788765712384, 7), 3)
        self.assertEqual(shard_for(18446744073709551615, 16), 15)

    def test_covers(self) -> None:
        s = ShardSettings(7, (3,))
        self.assertTrue(s.covers(81384788765712384))
        self.assertFalse(ShardSettings(7, (0, 1)).covers(81384788765712384))
