import unittest

from dmbot.campaigns import Campaign
from dmbot.consent_dm import CONSENT_LABEL, STOP_LABEL
from dmbot.transcription.config import Engine
from dmbot.ui.logic import (
    BUTTON_LABEL_MAX,
    CONTINUE_LABEL,
    HELP_TEXT,
    PHONE_LABEL_MAX,
    ago,
    backup_filename,
    can_run,
    default_voice_channel,
    option_description,
    played_line,
    runnable,
    settings_summary,
    shorten,
    writing_status,
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
        # The most-tapped button fits a phone; the message above it names the campaign.
        self.assertLessEqual(len(CONTINUE_LABEL), PHONE_LABEL_MAX)
        self.assertLessEqual(len(shorten("x" * 200, BUTTON_LABEL_MAX)), BUTTON_LABEL_MAX)
        self.assertEqual(shorten("  a   b ", 10), "a b")

    def test_settings_summary_is_plain(self) -> None:
        text = "\n".join(settings_summary("2024", "none", False, "private", "normal"))
        self.assertIn("2024 rules (newest)", text)
        self.assertIn("skip it, use only the main rules", text)
        self.assertIn(
            "use the 2014 rules (older)",
            "\n".join(settings_summary("2024", "2014", True, "peek", "normal")),
        )
        self.assertIn("off", text)
        self.assertIn("Only the DM", text)
        for jargon in ("fallback", "target", "ruleset", "visibility"):
            self.assertNotIn(jargon, text.lower())

    def test_backup_filename(self) -> None:
        self.assertEqual(
            backup_filename("Rime of the Frostmaiden!", NOW),
            "rime-of-the-frostmaiden-2027-01-15.dmbot.json.gz",
        )
        self.assertEqual(backup_filename("✨✨", NOW), "campaign-2027-01-15.dmbot.json.gz")


class MenuChoices(unittest.TestCase):
    def test_choices_explain_themselves_and_fit_a_phone(self) -> None:
        from dmbot.ui.logic import (
            OPTIONAL_RULES_CHOICES,
            PHONE_LABEL_MAX,
            chosen_label,
            fallback_choices,
            main_rules_choices,
            screen_note,
            visibility_choices,
        )

        self.assertEqual(main_rules_choices()["2024"], "Main rules: 2024")
        fb = fallback_choices("2024")
        self.assertNotIn("2024", fb)
        self.assertEqual(fb, {"2014": "If missing: use 2014", "none": "If missing: skip it"})
        for choices in (main_rules_choices(), fallback_choices("2014"), visibility_choices()):
            self.assertLessEqual(len(choices), 5)  # Discord: 5 buttons a row
        self.assertTrue(all(v.startswith("DM screen: ") for v in visibility_choices().values()))
        every = [
            *main_rules_choices().values(),
            *fallback_choices("2024").values(),
            *fallback_choices("2014").values(),
            *OPTIONAL_RULES_CHOICES.values(),
            *visibility_choices().values(),
        ]
        for label in every:  # #112: longer labels run off a phone's screen
            with self.subTest(label):
                self.assertLessEqual(len(chosen_label(label, True)), PHONE_LABEL_MAX)
        self.assertEqual(chosen_label("Main rules: 2024", True), "✓ Main rules: 2024")
        self.assertIn("peek", screen_note("peek"))
        self.assertEqual(screen_note("private"), "")

    def test_the_summary_starts_each_line_like_its_buttons(self) -> None:
        from dmbot.ui.logic import settings_summary

        lines = settings_summary("2024", "2014", True, "peek", "normal")
        for line, words in zip(
            lines,
            ("Main rules", "If missing", "Optional rules", "DM screen", "Normal"),
            strict=True,
        ):
            self.assertTrue(line.startswith(f"• **{words}"), line)

    def test_the_summary_says_what_each_level_does(self) -> None:
        from dmbot.ui.logic import settings_summary

        quiet = settings_summary("2024", "2014", True, "peek", "quiet")[-1]
        self.assertIn("fewer misheard names get fixed", quiet)
        self.assertIn("always show", quiet)
        self.assertTrue(quiet.startswith("• **Quiet** — how much DMbot says"), quiet)
        self.assertNotIn("recommended", quiet)
        normal = settings_summary("2024", "2014", True, "peek", "normal")[-1]
        self.assertIn("**Normal** (recommended) — how much DMbot says", normal)
        self.assertIn("press ⚙️ Settings on the pinned card in your DM screen", normal)

    def test_campaign_names_are_cut_for_a_phone(self) -> None:
        from dmbot.ui.logic import NAME_LABEL_MAX, name_label

        self.assertEqual(name_label("Rime of the Frostmaiden"), "Rime of the Frostmaiden")
        long = name_label("The Very Long and Winding Campaign of the Western Marches")
        self.assertLessEqual(len(long), NAME_LABEL_MAX)
        self.assertIn("…", long)
        self.assertTrue(long.endswith("tern Marches"))  # the end stays visible
        # Restored copies differ only at the end ("(restored 2)"): never the same label.
        base = "Curse of Strahd Fridays"
        labels = {name_label(f"{base}{end}") for end in ("", " (restored)", " (restored 2)")}
        self.assertEqual(len(labels), 3)
        self.assertTrue(all(len(label) <= NAME_LABEL_MAX for label in labels))
        self.assertEqual(name_label("  Spaced   out  "), "Spaced out")


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


class WritingStatus(unittest.TestCase):
    def test_off_never_says_keeping_up(self) -> None:
        # #39: with TRANSCRIBER=none, nothing is written down.
        line = writing_status("none", 0, 0.0)
        self.assertTrue(line.startswith("Writing things down: off."))
        self.assertNotIn("keeping up", line)
        self.assertIn("Whoever hosts DMbot", line)  # says who can change it
        self.assertNotIn("⚠️", line)  # a choice, not a fault

    def test_running_engine(self) -> None:
        running: tuple[Engine, ...] = ("whisper-local", "cloud")
        for engine in running:
            self.assertEqual(writing_status(engine, 0, None), "Writing things down: keeping up")
            self.assertEqual(writing_status(engine, 0, 0.4), "Writing things down: keeping up")
            self.assertEqual(
                writing_status(engine, 3, 2.6),
                "Writing things down: keeping up (about 3 s behind)",
            )
            self.assertIn("Writing things down: falling behind", writing_status(engine, 16, 1.0))

    def test_plain_words(self) -> None:
        engines: tuple[Engine, ...] = ("none", "whisper-local")
        for engine in engines:
            line = writing_status(engine, 0, None).lower()
            for jargon in ("transcri", "engine", "backlog", "whisper"):
                self.assertNotIn(jargon, line)


class HelpText(unittest.TestCase):
    def test_consent_is_asked_by_private_message(self) -> None:
        # #138: consent is a private message with buttons; the commands are fallbacks.
        self.assertIn("private message", HELP_TEXT)
        # Button names must match the buttons people actually see.
        self.assertIn(f"**{CONSENT_LABEL}**", HELP_TEXT)
        self.assertIn(f"**{STOP_LABEL}**", HELP_TEXT)
        self.assertIn("anyone in this server can read", HELP_TEXT.lower())
        self.assertIn("/consent give", HELP_TEXT)  # the fallback stays documented


class UploadLimit(unittest.TestCase):
    def test_a_boosted_server_takes_more_and_never_less_than_ten_megabytes(self) -> None:
        from dmbot.ui import logic

        self.assertEqual(logic.upload_limit(50 * 1024 * 1024), 50 * 1024 * 1024)
        self.assertEqual(logic.upload_limit(8 * 1024 * 1024), logic.FILE_MAX)
        self.assertEqual(logic.upload_limit(0), logic.FILE_MAX)
