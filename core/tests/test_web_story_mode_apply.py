"""The website's Shared story switch (#3078), applied by the bot the way Discord's switch is: the
store's own rules, the confirmation recorded with who chose, the store's words kept for the page
when it refuses. Invented names."""

from __future__ import annotations

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import STORY_NEEDS_CONFIRM
from dmbot.dm_screen import web_settings as ws
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE
from tests.test_web_settings import Settings


class TheStorySwitch(Settings):
    async def choose(self, value: str, *, flag: bool, who: int = ALICE.id) -> ws.Outcome:
        item = ws.Setting("a" * 32, self.one.id, who, ws.STORY_MODE, value, flag=flag)
        return await ws.apply(self.bot, self.g, item)

    async def mode(self) -> str:
        found: Campaign = await self.campaign()
        return found.story_mode

    async def confirmations(self) -> list[int]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT user_id FROM shared_confirmations WHERE guild_id = %s AND campaign_id = %s",
                (self.g, self.one.id),
            )
            return [int(r["user_id"]) for r in await cur.fetchall()]

    async def test_watch_and_guide_are_applied_and_the_choosing_dm_is_recorded(self) -> None:
        self.assertEqual((await self.choose("watch", flag=True)).error, "")
        self.assertEqual(await self.mode(), "watch")
        self.assertEqual(await self.confirmations(), [ALICE.id])
        self.assertEqual((await self.choose("guide", flag=True)).error, "")
        self.assertEqual(await self.mode(), "guide")
        self.assertEqual(await self.confirmations(), [ALICE.id])  # (once on record, not again)

    async def test_off_never_asks_and_keeps_the_record(self) -> None:
        self.assertEqual((await self.choose("off", flag=False)).error, "")
        self.assertEqual(await self.confirmations(), [])
        await self.choose("watch", flag=True)
        self.assertEqual((await self.choose("off", flag=False)).error, "")
        self.assertEqual(await self.mode(), "off")
        self.assertEqual(await self.confirmations(), [ALICE.id])

    async def test_turning_it_on_without_the_confirmation_is_refused_with_the_stores_words(
        self,
    ) -> None:
        done = await self.choose("watch", flag=False)
        self.assertEqual((done.error, done.note), (ws.REFUSED, STORY_NEEDS_CONFIRM))
        self.assertEqual(await self.mode(), "off")
        self.assertEqual(await self.confirmations(), [])

    async def test_a_confirmation_already_on_record_lets_it_turn_on_without_the_flag(self) -> None:
        await self.choose("watch", flag=True)
        await self.choose("off", flag=False)
        self.assertEqual((await self.choose("guide", flag=False)).error, "")
        self.assertEqual(await self.mode(), "guide")

    async def test_someone_who_is_no_longer_a_dm_changes_nothing(self) -> None:
        done = await self.choose("watch", flag=True, who=BOB.id)
        self.assertEqual(done.error, ws.NOT_ALLOWED)
        self.assertEqual(await self.mode(), "off")
        self.assertEqual(await self.confirmations(), [])

    async def test_a_word_the_switch_does_not_have_is_refused(self) -> None:
        self.assertEqual((await self.choose("loud", flag=True)).error, ws.BAD_VALUE)
        self.assertEqual(self.bot.calls, [])

    async def test_the_same_choice_twice_changes_nothing_more(self) -> None:
        await self.choose("watch", flag=True)
        self.assertEqual((await self.choose("watch", flag=True)).error, "")
        self.assertEqual(await self.mode(), "watch")
        self.assertEqual(await self.confirmations(), [ALICE.id])
