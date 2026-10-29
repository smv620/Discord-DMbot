"""The features table: the start-up line, the DMs' list and the settings button (#1831)."""

from __future__ import annotations

import dataclasses
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot import features
from dmbot.config import Settings, load_settings
from dmbot.dm_screen import settings as screen_settings
from dmbot.mail import MailSettings

DM, PLAYER, GUILD = 7, 8, 1


def settings(**overrides: Any) -> Settings:
    return dataclasses.replace(Settings(discord_token="t", ears_secret="s"), **overrides)


class TheTableTest(unittest.TestCase):
    def test_every_setting_is_a_feature_or_on_the_list_of_settings_that_are_not(self) -> None:
        covered = {name for f in features.FEATURES for name in f.covers}
        for field in dataclasses.fields(Settings):
            self.assertTrue(
                field.name in covered or field.name in features.NOT_A_SWITCH,
                f"{field.name}: add it to features.FEATURES, or to NOT_A_SWITCH with a reason",
            )

    def test_the_lists_hold_nothing_stale(self) -> None:
        names = {f.name for f in dataclasses.fields(Settings)}
        for feature in features.FEATURES:
            self.assertLessEqual(set(feature.covers), names, feature.log_name)
        self.assertLessEqual(set(features.NOT_A_SWITCH), names)
        both = {n for f in features.FEATURES for n in f.covers} & set(features.NOT_A_SWITCH)
        self.assertEqual(both, set())

    def test_every_bool_setting_is_covered_by_a_feature(self) -> None:
        covered = {name for f in features.FEATURES for name in f.covers}
        for field in dataclasses.fields(Settings):
            if field.type in (bool, "bool"):
                self.assertIn(field.name, covered)

    def test_each_feature_for_dms_has_a_sentence(self) -> None:
        for feature in features.FEATURES:
            if feature.for_dms:
                self.assertGreater(len(feature.dm_words), 10, feature.log_name)


class TheLineTest(unittest.TestCase):
    def test_the_line_lists_every_feature_with_on_or_off(self) -> None:
        line = features.features_line(settings(time_suggestions=True))
        self.assertTrue(line.startswith("Features: "))
        for feature in features.FEATURES:
            self.assertIn(f"{feature.log_name} o", line)
        self.assertIn("time suggestions on", line)
        self.assertIn("recaps off", line)

    def test_the_line_has_no_value_key_or_address(self) -> None:
        secret = settings(
            ai_key="sk-secret-key",
            elevenlabs_key="eleven-secret",
            elevenlabs_voice_id="voice-123",
            site_url="https://example.org/private",
            recaps_on=True,
            mail=MailSettings(
                host="smtp.example.org", sender="me@example.org", user="u", app_password="pw"
            ),
        )
        line = features.features_line(secret)
        for text in ("sk-secret", "eleven-secret", "voice-123", "example.org", "smtp", "pw"):
            self.assertNotIn(text, line)
        self.assertIn("ai helpers on", line)
        self.assertIn("recap audio on", line)
        self.assertIn("billing link on", line)

    def test_the_line_follows_the_environment(self) -> None:
        env = {
            "DISCORD_TOKEN": "t",
            "EARS_SHARED_SECRET": "s",
            "DATABASE_URL": "postgresql://x",
            "DMBOT_TIME_SUGGESTIONS": "1",
        }
        self.assertIn("time suggestions on", features.features_line(load_settings(env)))


class TheDMsListTest(unittest.TestCase):
    def test_what_is_on_comes_first_and_what_is_off_is_names_only(self) -> None:
        text = features.whats_on_text(settings(recaps_on=True))
        self.assertLess(text.index("Written recaps"), text.index("Not available right now"))
        self.assertIn("✅ **Written recaps**: DMbot writes a recap", text)
        self.assertIn("\n⬜ Clock suggestions\n", text)
        self.assertNotIn("not turned on by", text)
        self.assertTrue(text.endswith(features.FOOTER))

    def test_the_off_and_business_rows_stay_off_the_dms_list(self) -> None:
        text = features.whats_on_text(settings(recaps_on=True, enforce_plans=True, ai_key="k"))
        for hidden in ("Plan limits", "AI helpers", "Website link", "Email", "Test recordings"):
            self.assertNotIn(hidden, text)

    def test_nothing_on_has_one_plain_line_and_everything_on_has_no_off_block(self) -> None:
        none = settings(sidebar_recall=False, presession_note=False, secret_reveal_ask=False)
        self.assertEqual(features.whats_on_text(none), features.NOTHING_ON)
        every = settings(
            time_suggestions=True,
            recaps_on=True,
            elevenlabs_key="k",
            elevenlabs_voice_id="v",
            sidebar_on=True,
            story_extractor_on=True,
            live_heads_up=True,
            plot_check_on=True,
        )
        text = features.whats_on_text(every)
        self.assertNotIn("Not available right now", text)

    def test_no_value_is_shown(self) -> None:
        text = features.whats_on_text(settings(ai_key="sk-secret", site_url="https://example.org"))
        self.assertNotIn("sk-secret", text)
        self.assertNotIn("example.org", text)


class TheButtonTest(unittest.IsolatedAsyncioTestCase):
    def press(self, user: int, *, campaign: bool = True) -> Any:
        found = SimpleNamespace(dm_user_ids=frozenset({DM})) if campaign else None
        bot = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=found)),
            settings=settings(recaps_on=True),
        )
        return SimpleNamespace(
            client=bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=SimpleNamespace(id=user),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_a_dm_gets_the_list_privately(self) -> None:
        it = self.press(DM)
        await screen_settings.FeaturesButton("a" * 32).callback(it)
        (text,) = it.response.send_message.await_args.args
        self.assertIn("Written recaps", text)
        self.assertTrue(it.response.send_message.await_args.kwargs["ephemeral"])

    async def test_a_player_gets_a_refusal_and_not_the_list(self) -> None:
        it = self.press(PLAYER)
        await screen_settings.FeaturesButton("a" * 32).callback(it)
        (text,) = it.response.send_message.await_args.args
        self.assertEqual(text, screen_settings.ONLY_DM)
        self.assertTrue(it.response.send_message.await_args.kwargs["ephemeral"])

    async def test_a_campaign_that_is_gone_is_said_so(self) -> None:
        it = self.press(DM, campaign=False)
        await screen_settings.FeaturesButton("a" * 32).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], screen_settings.GONE)

    async def test_the_button_is_only_on_the_dms_card(self) -> None:
        from dmbot.campaigns.models import Campaign

        campaign = Campaign(
            "a" * 32,
            GUILD,
            "Rime",
            0,
            None,
            "2024",
            "2014",
            True,
            frozenset({DM}),
            DM,
            None,
            "peek",
        )
        for viewer, expected in ((DM, True), (PLAYER, False)):
            view = screen_settings.settings_view(campaign, None, viewer)
            ids = {str(getattr(getattr(c, "item", c), "custom_id", "")) for c in view.children}
            on_card = f"dmbot:features:{campaign.id}" in ids
            self.assertEqual(on_card, expected)
        rows = screen_settings.settings_view(campaign, None, DM).to_components()
        self.assertLessEqual(len(rows), 5)
        self.assertTrue(all(len(r["components"]) <= 5 for r in rows))


if __name__ == "__main__":
    unittest.main()
