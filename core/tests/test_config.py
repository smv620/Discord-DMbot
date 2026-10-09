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


class MemorySettings(unittest.TestCase):
    def test_undo_is_kept_30_days_by_default(self) -> None:
        self.assertEqual(load_settings(BASE).memory_keep_days, 30)

    def test_read(self) -> None:
        s = load_settings({**BASE, "MEMORY_CHANGELOG_KEEP_DAYS": " 7 "})
        self.assertEqual(s.memory_keep_days, 7)

    def test_bad_values(self) -> None:
        for raw in ("0", "-1", "x", "1.5", "²", "3651", "9" * 30):
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(ConfigError, "MEMORY_CHANGELOG_KEEP_DAYS"),
            ):
                load_settings({**BASE, "MEMORY_CHANGELOG_KEEP_DAYS": raw})


class PlanChecksSetting(unittest.TestCase):
    def test_off_by_default_with_no_site_address(self) -> None:
        s = load_settings(BASE)
        self.assertFalse(s.enforce_plans)
        self.assertEqual(s.site_url, "")

    def test_on_and_off_words(self) -> None:
        for raw, expected in (
            ("1", True),
            ("true", True),
            ("ON", True),
            ("0", False),
            ("off", False),
        ):
            self.assertEqual(
                load_settings({**BASE, "DMBOT_ENFORCE_PLANS": raw}).enforce_plans, expected
            )

    def test_a_typo_is_refused_not_guessed(self) -> None:
        with self.assertRaisesRegex(ConfigError, "DMBOT_ENFORCE_PLANS must be 1"):
            load_settings({**BASE, "DMBOT_ENFORCE_PLANS": "yes please"})

    def test_the_site_address_loses_its_trailing_slash(self) -> None:
        s = load_settings({**BASE, "WEB_SITE_URL": "https://dmbot.example/"})
        self.assertEqual(s.site_url, "https://dmbot.example")
