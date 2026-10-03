import unittest
from pathlib import Path

from dmbot.config import ConfigError, load_settings

BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s"}


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        s = load_settings(BASE)
        self.assertEqual((s.ears_host, s.ears_port, s.dev_guild_id), ("127.0.0.1", 8765, None))
        self.assertEqual(s.data_dir, Path("data"))

    def test_names_missing(self) -> None:
        with self.assertRaisesRegex(ConfigError, "DISCORD_TOKEN, EARS_SHARED_SECRET"):
            load_settings({})

    def test_bad_values(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "EARS_WS_PORT": "x"})
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DISCORD_DEV_GUILD_ID": "abc"})
