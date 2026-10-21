"""Importing for chosen servers only (#2259): the list parses strictly, the question is the same
for the bot and the website, and a bad value stops the start without showing an id."""

from __future__ import annotations

import unittest

from dmbot import imports_switch as sw
from dmbot.config import ConfigError, load_settings
from dmbot.web.settings import load_web_settings

BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}


class TheSwitch(unittest.TestCase):
    def test_off_everywhere_by_default(self) -> None:
        self.assertFalse(sw.enabled(False, sw.parse_servers(""), 5))
        self.assertFalse(load_settings(BASE).imports_enabled_for(5))

    def test_on_for_a_listed_server_and_off_for_another(self) -> None:
        listed = sw.parse_servers(" 11, 22,")
        self.assertEqual(listed, frozenset({11, 22}))
        self.assertTrue(sw.enabled(False, listed, 22))
        self.assertFalse(sw.enabled(False, listed, 33))
        got = load_settings({**BASE, "DMBOT_IMPORTS_SERVERS": "11,22"})
        self.assertEqual((got.imports_enabled_for(11), got.imports_enabled_for(33)), (True, False))

    def test_the_main_switch_still_turns_it_on_for_all(self) -> None:
        self.assertTrue(sw.enabled(True, frozenset(), 99))
        self.assertTrue(load_settings({**BASE, "DMBOT_IMPORTS": "1"}).imports_enabled_for(99))

    def test_a_bad_value_stops_the_start_and_never_shows_an_id(self) -> None:
        for bad in ("abc", "11,x", "0", "-5", "1" * 25, "１２"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as caught:
                    sw.parse_servers(bad)
                self.assertIn("DMBOT_IMPORTS_SERVERS", str(caught.exception))
                self.assertNotIn(bad, str(caught.exception))
                with self.assertRaisesRegex(ConfigError, "DMBOT_IMPORTS_SERVERS"):
                    load_settings({**BASE, "DMBOT_IMPORTS_SERVERS": bad})
        with self.assertRaisesRegex(ConfigError, "DMBOT_IMPORTS must be"):
            load_settings({**BASE, "DMBOT_IMPORTS": "maybe"})

    def test_the_ids_are_not_in_what_a_settings_object_prints(self) -> None:
        got = load_settings({**BASE, "DMBOT_IMPORTS_SERVERS": "123456789012345"})
        self.assertNotIn("123456789012345", repr(got))


class TheWebSide(unittest.TestCase):
    def test_the_website_reads_the_same_setting(self) -> None:
        from tests import test_web_api as wa

        env = wa.Settings.ENV
        got = load_web_settings({**env, "DMBOT_IMPORTS_SERVERS": "7"})
        self.assertEqual((got.imports_enabled_for(7), got.imports_enabled_for(8)), (True, False))
        with self.assertRaisesRegex(ConfigError, "DMBOT_IMPORTS_SERVERS"):
            load_web_settings({**env, "DMBOT_IMPORTS_SERVERS": "nope"})
        self.assertNotIn("imports_servers", repr(got))  # (kept out of what is printed)


if __name__ == "__main__":
    unittest.main()
