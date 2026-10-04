import unittest

from dmbot.campaigns import Campaign
from dmbot.ui.logic import (
    BUTTON_LABEL_MAX,
    ago,
    backup_filename,
    can_run,
    continue_label,
    default_voice_channel,
    option_description,
    played_line,
    runnable,
    settings_summary,
    shorten,
)

NOW = 1_800_000_000


def campaign(**kw: object) -> Campaign:
    base: dict[str, object] = {
        "id": "c1",
        "guild_id": 1,
        "name": "Rime of the Frostmaiden",
        "created_at": NOW - 86400 * 30,
        "last_played_at": NOW - 3600 * 3,
        "target_ruleset": "2024",
        "fallback_ruleset": "2014",
        "optional_rules_default": True,
        "dm_user_ids": frozenset({7}),
        "dm_screen_channel_id": None,
        "last_voice_channel_id": 50,
        "dm_screen_visibility": "peek",
    }
    base.update(kw)
    return Campaign(**base)  # type: ignore[arg-type]


class Permissions(unittest.TestCase):
    def test_dm_or_server_manager(self) -> None:
        c = campaign()
        self.assertTrue(can_run(c, 7, False))
        self.assertFalse(can_run(c, 8, False))
        self.assertTrue(can_run(c, 8, True))

    def test_runnable_filters(self) -> None:
        a, b = campaign(id="a"), campaign(id="b", dm_user_ids=frozenset({9}))
        self.assertEqual([c.id for c in runnable([a, b], 7, False)], ["a"])
        self.assertEqual([c.id for c in runnable([a, b], 7, True)], ["a", "b"])


class Wording(unittest.TestCase):
    def test_ago(self) -> None:
        cases = {
            0: "just now",
            59: "just now",
            60: "1 minute ago",
            3599: "59 minutes ago",
            3600: "1 hour ago",
            7200: "2 hours ago",
            86400: "yesterday",
            86400 * 5: "5 days ago",
            86400 * 21: "3 weeks ago",
        }
        for seconds, text in cases.items():
            self.assertEqual(ago(NOW - seconds, NOW), text, seconds)
        self.assertEqual(ago(NOW + 50, NOW), "just now")  # clock skew
        self.assertRegex(ago(NOW - 86400 * 400, NOW), r"^[A-Z][a-z]{2} \d{4}$")

    def test_played_line_uses_discord_local_time(self) -> None:
        self.assertEqual(played_line(campaign()), f"last played <t:{NOW - 10800}:f>")
        self.assertEqual(played_line(campaign(last_played_at=None)), "not played yet")

    def test_option_description(self) -> None:
        self.assertEqual(option_description(campaign(), NOW), "Last played 3 hours ago")
        self.assertEqual(option_description(campaign(last_played_at=None), NOW), "Not played yet")

    def test_labels_fit_discord_limits(self) -> None:
        label = continue_label(campaign(name="x" * 200))
        self.assertLessEqual(len(label), BUTTON_LABEL_MAX)
        self.assertTrue(label.endswith("…"))
        self.assertEqual(shorten("  a   b ", 10), "a b")

    def test_settings_summary_is_plain(self) -> None:
        text = "\n".join(settings_summary("2024", "none", False, "private"))
        self.assertIn("2024 rules (newest)", text)
        self.assertIn("None (main rules only)", text)
        self.assertIn("off", text)
        self.assertIn("Only the DM", text)
        for jargon in ("fallback", "target", "ruleset", "visibility"):
            self.assertNotIn(jargon, text.lower())

    def test_backup_filename(self) -> None:
        self.assertEqual(
            backup_filename("Rime of the Frostmaiden!", NOW),
            "rime-of-the-frostmaiden-2027-01-15.dmbot.json",
        )
        self.assertEqual(backup_filename("✨✨", NOW), "campaign-2027-01-15.dmbot.json")


class MenuChoices(unittest.TestCase):
    def test_choices_explain_themselves(self) -> None:
        from dmbot.ui.logic import (
            OPTIONAL_RULES_CHOICES,
            fallback_choices,
            main_rules_choices,
            screen_note,
            visibility_choices,
        )

        self.assertEqual(main_rules_choices()["2024"], "Main rules: 2024 rules (newest)")
        fb = fallback_choices("2024")
        self.assertNotIn("2024", fb)
        self.assertTrue(all(v.startswith("If the main rules don't cover it") for v in fb.values()))
        self.assertIn("none", fb)
        self.assertTrue(all(v.startswith("DM screen: ") for v in visibility_choices().values()))
        self.assertTrue(
            all(len(v) <= 100 for v in [*fb.values(), *OPTIONAL_RULES_CHOICES.values()])
        )
        self.assertIn("peek", screen_note("peek"))
        self.assertEqual(screen_note("private"), "")


class VoiceDefault(unittest.TestCase):
    def test_prefers_last_time_then_current(self) -> None:
        c = campaign(last_voice_channel_id=50)
        self.assertEqual(default_voice_channel(c, 60, {50, 60}), 50)
        self.assertEqual(default_voice_channel(c, 60, {60}), 60)  # last one is gone
        self.assertIsNone(default_voice_channel(c, 70, {60}))
        self.assertEqual(default_voice_channel(None, 60, {60}), 60)
        self.assertIsNone(default_voice_channel(None, None, {60}))


class BackupName(unittest.TestCase):
    def test_reads_name_safely(self) -> None:
        from dmbot.ui.logic import backup_campaign_name, dm_list

        self.assertEqual(backup_campaign_name({"campaign": {"name": " Frost "}}), "Frost")
        bads: list[object] = [
            None,
            [],
            {"campaign": 5},
            {"campaign": {"name": 5}},
            {"campaign": {"name": " "}},
        ]
        for bad in bads:
            self.assertIsNone(backup_campaign_name(bad))
        self.assertEqual(dm_list(campaign(dm_user_ids=frozenset({9, 7}))), "<@7>, <@9>")
