import unittest
from pathlib import Path

from dmbot.config import ConfigError, load_settings

BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        s = load_settings(BASE)
        self.assertEqual((s.ears_host, s.ears_port, s.dev_guild_id), ("127.0.0.1", 8765, None))
        self.assertEqual(s.data_dir, Path("data"))

    def test_names_missing(self) -> None:
        with self.assertRaisesRegex(ConfigError, "DISCORD_TOKEN, EARS_SHARED_SECRET, DATABASE_URL"):
            load_settings({})

    def test_bad_values(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "EARS_WS_PORT": "x"})
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DISCORD_DEV_GUILD_ID": "abc"})


class ShardAndLogSettings(unittest.TestCase):
    def test_defaults(self) -> None:
        from dmbot.sharding import ShardSettings

        s = load_settings(BASE)
        self.assertEqual(s.shards, ShardSettings(1, (0,)))
        self.assertEqual((s.log_format, s.log_level), ("text", "INFO"))

    def test_read(self) -> None:
        s = load_settings(
            {
                **BASE,
                "SHARD_COUNT": "4",
                "SHARD_IDS": "2,3",
                "LOG_FORMAT": "JSON",
                "LOG_LEVEL": "debug",
            }
        )
        self.assertEqual((s.shards.count, s.shards.ids), (4, (2, 3)))
        self.assertEqual((s.log_format, s.log_level), ("json", "DEBUG"))

    def test_bad_values(self) -> None:
        for env, message in (
            ({"SHARD_COUNT": "x"}, "SHARD_COUNT"),
            ({"SHARD_COUNT": "2", "SHARD_IDS": "5"}, "SHARD_IDS"),
            ({"LOG_FORMAT": "xml"}, "LOG_FORMAT"),
            ({"LOG_LEVEL": "loud"}, "LOG_LEVEL"),
        ):
            with self.subTest(env=env), self.assertRaisesRegex(ConfigError, message):
                load_settings({**BASE, **env})
