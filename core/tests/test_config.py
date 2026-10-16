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


class SidebarRecall(unittest.TestCase):
    def test_the_sidebar_uses_the_confirmed_story_unless_switched_off(self) -> None:
        s = load_settings(BASE)
        self.assertEqual((s.sidebar_recall, s.sidebar_recall_budget), (True, 1000))
        off = load_settings(
            {**BASE, "DMBOT_SIDEBAR_RECALL": "0", "DMBOT_SIDEBAR_RECALL_BUDGET": "400"}
        )
        self.assertEqual((off.sidebar_recall, off.sidebar_recall_budget), (False, 400))
        for name, bad in (
            ("DMBOT_SIDEBAR_RECALL", "maybe"),
            ("DMBOT_SIDEBAR_RECALL_BUDGET", "199"),
            ("DMBOT_SIDEBAR_RECALL_BUDGET", "4001"),
            ("DMBOT_SIDEBAR_RECALL_BUDGET", "lots"),
        ):
            with self.assertRaisesRegex(ConfigError, name):
                load_settings({**BASE, name: bad})


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

    def test_the_sidebar_is_off_unless_switched_on(self) -> None:
        self.assertFalse(load_settings(BASE).sidebar_on)
        self.assertTrue(load_settings({**BASE, "DMBOT_SIDEBAR": "1"}).sidebar_on)
        self.assertFalse(load_settings({**BASE, "DMBOT_SIDEBAR": "off"}).sidebar_on)
        self.assertFalse(load_settings(BASE).story_extractor_on)  # off unless asked for
        self.assertTrue(load_settings({**BASE, "DMBOT_STORY_EXTRACTOR": "1"}).story_extractor_on)
        with self.assertRaisesRegex(ConfigError, "DMBOT_STORY_EXTRACTOR must be 1"):
            load_settings({**BASE, "DMBOT_STORY_EXTRACTOR": "maybe"})

    def test_the_recall_budget_has_a_default_and_is_checked(self) -> None:
        self.assertEqual(load_settings(BASE).recall_budget, 4000)
        self.assertEqual(load_settings({**BASE, "DMBOT_RECALL_BUDGET": "9000"}).recall_budget, 9000)
        for bad in ("100", "50001", "lots", "-5"):
            with self.subTest(bad), self.assertRaisesRegex(ConfigError, "DMBOT_RECALL_BUDGET"):
                load_settings({**BASE, "DMBOT_RECALL_BUDGET": bad})

    def test_the_extractors_tier_and_call_limit(self) -> None:
        s = load_settings(BASE)
        self.assertEqual((s.story_extractor_tier, s.story_extractor_max_calls), ("fast", 480))
        self.assertEqual(s.story_extractor_max_calls_day, 960)
        s = load_settings(
            {
                **BASE,
                "DMBOT_STORY_EXTRACTOR_TIER": "Careful",
                "DMBOT_STORY_EXTRACTOR_MAX_CALLS": "9",
            }
        )
        self.assertEqual((s.story_extractor_tier, s.story_extractor_max_calls), ("careful", 9))
        for name, bad in (
            ("DMBOT_STORY_EXTRACTOR_TIER", "best"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS", "0"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS", "5001"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS", "many"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS", "²"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS_DAY", "0"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS_DAY", "50001"),
            ("DMBOT_STORY_EXTRACTOR_MAX_CALLS_DAY", "lots"),
        ):
            with self.assertRaisesRegex(ConfigError, name):
                load_settings({**BASE, name: bad})

    def test_continuity_levels_have_defaults_and_are_checked(self) -> None:
        levels = load_settings(BASE).continuity
        self.assertEqual((levels.possible, levels.probable, levels.likely), (0.25, 0.5, 0.8))
        self.assertEqual((levels.min_facts, levels.min_sessions), (50, 3))
        changed = load_settings({**BASE, "DMBOT_CONTINUITY_MIN_FACTS": "20"}).continuity
        self.assertEqual(changed.min_facts, 20)
        for name, bad in (
            ("DMBOT_CONTINUITY_LIKELY", "1.5"),
            ("DMBOT_CONTINUITY_POSSIBLE", "none"),
            ("DMBOT_CONTINUITY_MIN_SESSIONS", "0"),
        ):
            with self.subTest(name), self.assertRaisesRegex(ConfigError, name):
                load_settings({**BASE, name: bad})
        with self.assertRaisesRegex(ConfigError, "in that order"):
            load_settings({**BASE, "DMBOT_CONTINUITY_POSSIBLE": "0.9"})
        with self.assertRaisesRegex(ConfigError, "DMBOT_SIDEBAR must be 1"):
            load_settings({**BASE, "DMBOT_SIDEBAR": "maybe"})

    def test_the_site_address_loses_its_trailing_slash(self) -> None:
        s = load_settings({**BASE, "WEB_SITE_URL": "https://dmbot.example/"})
        self.assertEqual(s.site_url, "https://dmbot.example")
