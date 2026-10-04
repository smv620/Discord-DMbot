"""The recording notice: posted once per table, retried and reported if it fails."""

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.ears.protocol import Status
from dmbot.sessions import SessionStore
from tests.pg import DatabaseTest

GUILD, VOICE, SCREEN = 1, 2, 3


class RecordingNoticeTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            CampaignStore(self.db),
            SessionStore(self.db),
        )
        self.posts: list[tuple[int, str]] = []
        self.voice_ok = True

        async def fake_post(channel_id: int, text: str) -> bool:
            self.posts.append((channel_id, text))
            return channel_id != VOICE or self.voice_ok

        self.bot.post = fake_post  # type: ignore[method-assign]
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=9, segmenter=Segmenter(GUILD))

    async def joined(self) -> None:
        await self.bot._on_status(self.table, Status("joined", guild_id=GUILD))

    def voice_posts(self) -> list[str]:
        return [text for cid, text in self.posts if cid == VOICE]

    def screen_warnings(self) -> list[str]:
        return [t for cid, t in self.posts if cid == SCREEN and "weren't told" in t]

    async def test_notice_posts_once_even_after_reconnect(self) -> None:
        await self.joined()
        self.table.listening = False  # ears link dropped
        await self.joined()
        self.assertEqual(len(self.voice_posts()), 1)
        self.assertTrue(self.table.notice_posted)
        self.assertEqual(self.screen_warnings(), [])

    async def test_failed_notice_warns_dm_and_retries_next_join(self) -> None:
        self.voice_ok = False
        await self.joined()
        self.assertFalse(self.table.notice_posted)
        self.assertEqual(len(self.screen_warnings()), 1)

        self.voice_ok = True
        self.table.listening = False  # the voice connection dropped and came back
        await self.joined()
        self.assertTrue(self.table.notice_posted)
        self.assertEqual(len(self.voice_posts()), 2)  # first attempt + successful retry
        self.assertEqual(len(self.screen_warnings()), 1)

    async def test_warning_repeats_while_notice_keeps_failing(self) -> None:
        self.voice_ok = False
        await self.joined()
        self.table.listening = False  # the voice connection dropped and came back
        await self.joined()
        self.assertEqual(len(self.screen_warnings()), 2)

    async def test_a_repeat_joined_says_nothing_new(self) -> None:
        await self.joined()
        count = len(self.posts)
        await self.joined()  # ears confirmed again without a drop in between
        self.assertEqual(len(self.posts), count)
