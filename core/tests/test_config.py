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
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DISCORD_CLEAR_GUILD_COMMANDS": "1,abc"})

    def test_the_servers_to_clear_are_listed_with_commas(self) -> None:
        self.assertEqual(load_settings(BASE).clear_guild_commands, ())
        got = load_settings({**BASE, "DISCORD_CLEAR_GUILD_COMMANDS": "11, 22,"})
        self.assertEqual(got.clear_guild_commands, (11, 22))


class PoolSize(unittest.TestCase):
    def test_ten_unless_set_so_nothing_changes_for_a_deployment_that_sets_nothing(self) -> None:
        self.assertEqual(load_settings(BASE).database_pool_max, 10)
        self.assertEqual(load_settings({**BASE, "DATABASE_POOL_MAX": ""}).database_pool_max, 10)

    def test_a_pod_can_take_a_smaller_or_larger_pool(self) -> None:
        for raw, want in (("4", 4), ("1", 1), ("100", 100)):
            self.assertEqual(
                load_settings({**BASE, "DATABASE_POOL_MAX": raw}).database_pool_max, want
            )

    def test_nonsense_is_refused_with_a_plain_message(self) -> None:
        for raw in ("0", "101", "-2", "ten", "4.5", "1000", "\u00b2"):
            with self.assertRaisesRegex(ConfigError, "DATABASE_POOL_MAX must be a number"):
                load_settings({**BASE, "DATABASE_POOL_MAX": raw})


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
        self.assertFalse(load_settings(BASE).story_readings_on)  # off unless asked
        self.assertTrue(load_settings({**BASE, "DMBOT_STORY_READINGS": "1"}).story_readings_on)
        with self.assertRaisesRegex(ConfigError, "DMBOT_STORY_READINGS must be 1"):
            load_settings({**BASE, "DMBOT_STORY_READINGS": "maybe"})

    def test_grading_contradictions_is_off_unless_asked(self) -> None:
        self.assertFalse(load_settings(BASE).story_contradictions_on)
        self.assertTrue(
            load_settings({**BASE, "DMBOT_STORY_CONTRADICTIONS": "1"}).story_contradictions_on
        )
        with self.assertRaisesRegex(ConfigError, "DMBOT_STORY_CONTRADICTIONS must be 1"):
            load_settings({**BASE, "DMBOT_STORY_CONTRADICTIONS": "maybe"})
        with self.assertRaisesRegex(ConfigError, "DMBOT_STORY_EXTRACTOR must be 1"):
            load_settings({**BASE, "DMBOT_STORY_EXTRACTOR": "maybe"})

    def test_recaps_are_off_unless_switched_on(self) -> None:
        self.assertFalse(load_settings(BASE).recaps_on)
        self.assertTrue(load_settings({**BASE, "DMBOT_RECAPS": "1"}).recaps_on)
        with self.assertRaisesRegex(ConfigError, "DMBOT_RECAPS must be 1"):
            load_settings({**BASE, "DMBOT_RECAPS": "sometimes"})

    def test_recap_audio_needs_the_switch_a_key_and_a_voice_and_is_checked(self) -> None:
        on = {**BASE, "DMBOT_RECAPS": "1"}
        self.assertFalse(load_settings(on).recap_audio_on)  # no key: text-only
        self.assertFalse(
            load_settings({**on, "ELEVENLABS_API_KEY": "k"}).recap_audio_on
        )  # no voice
        both = {**on, "ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "abc123"}
        self.assertTrue(load_settings(both).recap_audio_on)
        self.assertFalse(load_settings({**both, "DMBOT_RECAPS": "0"}).recap_audio_on)
        self.assertNotIn("elevenlabs_key", repr(load_settings(both)))  # the key is never shown
        self.assertEqual(load_settings(BASE).recap_audio_max_credits, 15000)
        self.assertEqual(load_settings(BASE).elevenlabs_speech_model, "eleven_v3")
        self.assertEqual(
            load_settings({**BASE, "ELEVENLABS_SPEECH_MODEL": "eleven_v4"}).elevenlabs_speech_model,
            "eleven_v4",
        )
        with self.assertRaisesRegex(ConfigError, "ELEVENLABS_SPEECH_MODEL"):
            load_settings({**BASE, "ELEVENLABS_SPEECH_MODEL": "../x"})
        with self.assertRaisesRegex(ConfigError, "DMBOT_RECAP_AUDIO_MAX_CREDITS"):
            load_settings({**BASE, "DMBOT_RECAP_AUDIO_MAX_CREDITS": "5"})
        with self.assertRaisesRegex(ConfigError, "ELEVENLABS_VOICE_ID"):
            load_settings({**BASE, "ELEVENLABS_VOICE_ID": "../x"})

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
