"""The one rule for "the table's chat" (#3212), pinned directly: a campaign's and a table's chat
channel, with the voice channel as the fallback (#3279)."""

from __future__ import annotations

import unittest

from dmbot.audio.segmenter import Segmenter
from dmbot.campaigns.models import Campaign
from dmbot.table import Table

GUILD, DM = 1234, 7
VOICE, PICKED, LAST = 111, 222, 333
CAMPAIGN = "c" * 32


def campaign(last_voice: int | None, picked: int | None) -> Campaign:
    return Campaign(
        id=CAMPAIGN,
        guild_id=GUILD,
        name="Frostmaiden",
        created_at=0,
        last_played_at=None,
        target_ruleset="2024",
        fallback_ruleset="2014",
        optional_rules_default=True,
        dm_user_ids=frozenset({DM}),
        dm_screen_channel_id=None,
        last_voice_channel_id=last_voice,
        dm_screen_visibility="peek",
        table_chat_channel_id=picked,
    )


def table(chat: int | None = None) -> Table:
    return Table(
        guild_id=GUILD,
        voice_channel_id=VOICE,
        screen_channel_id=555,
        dm_user_id=DM,
        segmenter=Segmenter(GUILD),
        chat_channel_id=chat,
    )


class TheTablesChat(unittest.TestCase):
    def test_the_picked_channel_wins_for_a_campaign(self) -> None:
        self.assertEqual(campaign(last_voice=LAST, picked=PICKED).table_chat_id, PICKED)

    def test_the_voice_channel_is_the_fallback_for_a_campaign(self) -> None:
        self.assertEqual(campaign(last_voice=LAST, picked=None).table_chat_id, LAST)

    def test_a_campaign_with_neither_has_no_chat(self) -> None:
        self.assertIsNone(campaign(last_voice=None, picked=None).table_chat_id)

    def test_the_picked_channel_wins_for_a_table(self) -> None:
        self.assertEqual(table(chat=PICKED).chat_id, PICKED)

    def test_a_table_falls_back_to_its_voice_channel(self) -> None:
        self.assertEqual(table(chat=None).chat_id, VOICE)

    def test_a_table_follows_the_dms_choice_live(self) -> None:
        live = table(chat=PICKED)
        self.assertEqual(live.chat_id, PICKED)
        live.chat_channel_id = None  # the DM un-picks the channel
        self.assertEqual(live.chat_id, VOICE)


if __name__ == "__main__":
    unittest.main()
