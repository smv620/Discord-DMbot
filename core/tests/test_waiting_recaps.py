"""A finished recap the DM never decided on is said once at the next session start (#1474), with the
same buttons (#1457): only a ready draft, at most two, newest first; never a posted, skipped
or unfinished one; nothing when the campaign's recaps are off or the session resumed."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.campaigns.store import CampaignStore
from dmbot.recaps.store import RecapStore
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest
from tests.test_recap_jobs import Clock

GUILD = 1
CID_LEN = 32


class TheStoresList(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.one = (await self.campaigns.create(GUILD, "One", fx.DM)).id
        self.two = (await self.campaigns.create(GUILD, "Two", fx.DM)).id
        self.store = RecapStore(self.db, clock=Clock())

    async def make(self, campaign: str, at: int, *, state: str = "ready", title: str = "T") -> None:
        await self.store.enqueue(GUILD, campaign, at, dm_user_id=fx.DM)
        await self.store.update(GUILD, campaign, at, state=state, title=f"{title}{at}")

    async def test_only_ready_drafts_newest_first_at_most_the_limit(self) -> None:
        for at in (100, 200, 300):
            await self.make(self.one, at)
        await self.make(self.one, 400, state="beats")  # still being written
        await self.make(self.one, 500)
        await self.store.decide(GUILD, self.one, 500, "drop")  # skipped
        await self.make(self.one, 600)
        await self.store.decide(GUILD, self.one, 600, "post")  # being posted
        await self.make(self.two, 700)  # another campaign's
        got = await self.store.waiting_drafts(GUILD, self.one)
        self.assertEqual(got, [(300, "T300"), (200, "T200")])
        self.assertEqual(len(await self.store.waiting_drafts(GUILD, self.one, limit=5)), 3)
        self.assertEqual(await self.store.waiting_drafts(GUILD, "0" * CID_LEN), [])


def fake_bot(drafts: list[tuple[int, str]], *, on: bool = True, text: bool = True) -> Any:
    table = SimpleNamespace(
        guild_id=GUILD, campaign_id="c" * CID_LEN, screen_channel_id=66, resumed=False
    )
    bot = SimpleNamespace(
        recaps=SimpleNamespace(waiting_drafts=AsyncMock(return_value=drafts)),
        settings=SimpleNamespace(recaps_on=on, site_url="https://site.example.com"),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(recaps_text=text))),
        tables={GUILD: table},
        post=AsyncMock(return_value=True),
        recap_audio_why=AsyncMock(return_value=""),
    )
    return bot, table


async def run(bot: Any, table: Any) -> None:
    await DMBot._waiting_recaps(cast(Any, bot), table)


class TheStartLines(unittest.IsolatedAsyncioTestCase):
    async def test_a_waiting_draft_is_said_once_with_its_buttons(self) -> None:
        bot, table = fake_bot([(300, "The Silver Lock")])
        await run(bot, table)
        ((channel, line), kwargs) = bot.post.await_args.args, bot.post.await_args.kwargs
        self.assertEqual(channel, 66)
        self.assertEqual(
            line,
            "📜 Last session's recap is still waiting: **The Silver Lock**. "
            "Nothing has gone to the players.",
        )
        # the three ways to approve (#2420), the website link, then Share a recap (#2118)
        children = kwargs["view"].children
        self.assertEqual(
            [cast(Any, c).item.custom_id.split(":")[2] for c in children[:3]], ["ap", "aa", "ak"]
        )
        self.assertTrue(children[3].url.startswith("https://site.example.com/account/recaps"))
        self.assertEqual(cast(Any, children[4]).item.custom_id, f"dmbot:recapshare:{'c' * 32}")
        self.assertEqual(bot.post.await_count, 1)

    async def test_at_most_two_one_line_each_newest_first(self) -> None:
        bot, table = fake_bot([(300, "Third"), (200, "Second")])
        await run(bot, table)
        lines = [c.args[1] for c in bot.post.await_args_list]
        self.assertEqual(
            [("Third" in x, "Second" in x) for x in lines], [(True, False), (False, True)]
        )
        bot.recaps.waiting_drafts.assert_awaited_once_with(GUILD, "c" * 32)

    async def test_nothing_when_nothing_is_waiting(self) -> None:
        bot, table = fake_bot([])
        await run(bot, table)
        bot.post.assert_not_awaited()

    async def test_nothing_when_recaps_are_off_for_the_bot_or_the_campaign_or_after_a_restart(
        self,
    ) -> None:
        for kw in ({"on": False}, {"text": False}):
            bot, table = fake_bot([(300, "T")], **kw)
            await run(bot, table)
            bot.post.assert_not_awaited()
        bot, table = fake_bot([(300, "T")])
        table.resumed = True
        await run(bot, table)
        bot.post.assert_not_awaited()
        bot.recaps = None
        table.resumed = False
        await run(bot, table)

    async def test_a_session_that_ended_meanwhile_gets_no_line(self) -> None:
        bot, table = fake_bot([(300, "T")])
        bot.tables = {}
        await run(bot, table)
        bot.post.assert_not_awaited()

    async def test_a_title_cannot_break_the_markdown(self) -> None:
        bot, table = fake_bot([(300, "*Bold* _x_")])
        await run(bot, table)
        self.assertIn("\\*Bold\\*", bot.post.await_args.args[1])

    async def test_a_failure_costs_the_line_never_the_session(self) -> None:
        bot, table = fake_bot([(300, "T")])
        bot.recaps.waiting_drafts = AsyncMock(side_effect=RuntimeError("down"))
        await run(bot, table)  # no exception
        bot.post.assert_not_awaited()
