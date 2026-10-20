"""Share a recap (#2118) on Discord: DMs only on every press, the picker, the link after Post's
checks, and the send to the chat channel. Invented fixtures only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.ui import recap_share as rs
from tests import recap_fixtures as fx
from tests.test_recap_jobs import Base

GUILD, DM_ID, PLAYER, CHAT = 5, 7, 11, 321
CID = "c" * 32
TOKEN = "T" * 43


def make(user: int = DM_ID, *, recent: Any = None, link: Any = (TOKEN, "")) -> Any:
    campaign = SimpleNamespace(
        guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}), dmbchat_channel_id=CHAT
    )
    channel = SimpleNamespace(send=AsyncMock(), guild=SimpleNamespace(id=GUILD))
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        recaps=SimpleNamespace(
            recent=AsyncMock(
                return_value=[(100, "The Silver Lock", "posted")] if recent is None else recent
            ),
            number_of=AsyncMock(return_value=3),
        ),
        settings=SimpleNamespace(site_url="https://site.example.com"),
        recap_share_link=AsyncMock(return_value=link),
        get_channel=lambda cid: channel if cid == CHAT else None,
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=user),
        response=SimpleNamespace(
            is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return bot, interaction, channel


def said(interaction: Any) -> str:
    return str(interaction.response.send_message.await_args.args[0])


class TheShareButton(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_is_refused_on_every_step(self) -> None:
        bot, interaction, channel = make(PLAYER)
        await rs.ShareButton(CID).callback(interaction)
        self.assertEqual(said(interaction), rs.NOT_YOURS)
        for step in (rs.HowView(CID, 100).copy, rs.HowView(CID, 100).send):
            bot, interaction, channel = make(PLAYER)
            await step.callback(interaction)
            self.assertEqual(said(interaction), rs.NOT_YOURS)
            bot.recap_share_link.assert_not_awaited()
            channel.send.assert_not_awaited()

    async def test_the_dm_gets_a_menu_of_the_recaps_newest_first(self) -> None:
        recent = [(300 - n, f"Recap {n}", "posted") for n in range(30)]
        _bot, interaction, _ = make(recent=recent)
        await rs.ShareButton(CID).callback(interaction)
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(len(view.select.options), 25)  # Discord's limit for a menu
        self.assertEqual(view.select.options[0].label, "Recap 0")

    async def test_no_recaps_or_no_site_says_so(self) -> None:
        bot, interaction, _ = make(recent=[])
        bot.recaps.recent = AsyncMock(return_value=[])
        await rs.ShareButton(CID).callback(interaction)
        self.assertEqual(said(interaction), rs.NO_RECAPS)
        bot, interaction, _ = make()
        bot.settings.site_url = ""
        await rs.ShareButton(CID).callback(interaction)
        self.assertEqual(said(interaction), rs.NO_SITE)

    async def test_copy_the_link_replies_privately_with_the_link(self) -> None:
        _bot, interaction, channel = make()
        await rs.HowView(CID, 100).copy.callback(interaction)
        sent = interaction.followup.send.await_args
        self.assertEqual(sent.args[0], f"https://site.example.com/r/{TOKEN}")
        self.assertTrue(sent.kwargs["ephemeral"])
        channel.send.assert_not_awaited()

    async def test_send_posts_the_link_in_the_chat_channel_and_nothing_else(self) -> None:
        _bot, interaction, channel = make()
        await rs.HowView(CID, 100).send.callback(interaction)
        channel.send.assert_awaited_once()
        self.assertEqual(
            channel.send.await_args.args[0],
            f"📜 Recap of session 3: https://site.example.com/r/{TOKEN}",
        )

    async def test_a_recap_that_fails_the_checks_gets_no_link_and_nothing_is_sent(self) -> None:
        _bot, interaction, channel = make(link=(None, "It names something you keep secret."))
        await rs.HowView(CID, 100).send.callback(interaction)
        channel.send.assert_not_awaited()
        self.assertEqual(
            interaction.followup.send.await_args.args[0], "It names something you keep secret."
        )

    async def test_no_chat_channel_says_so_before_any_link_is_made(self) -> None:
        bot, interaction, _channel = make()
        bot.get_channel = lambda _cid: None
        await rs.HowView(CID, 100).send.callback(interaction)
        self.assertEqual(said(interaction), rs.NO_CHAT)
        bot.recap_share_link.assert_not_awaited()

    def test_the_id_survives_a_restart_and_fits(self) -> None:
        self.assertEqual(rs.ShareButton(CID).item.custom_id, f"dmbot:recapshare:{CID}")
        self.assertTrue(callable(DMBot.recap_share_link))


if __name__ == "__main__":
    unittest.main()


class TheStoreSide(Base):
    async def finished(self, at: int, title: str = "T") -> None:
        await self.store.add_finished(
            1, self.one.id, at, dm_user_id=fx.DM, title=title, summary="s", original="s",
            housekeeping=[], music=[], game_day=None, speakers=[], grade={}, beats=None,
        )  # fmt: skip

    async def test_recent_lists_ready_drafts_and_posted_newest_first_and_numbers_them(self) -> None:
        for at in (100, 200, 300):
            await self.finished(at, f"Recap {at}")
        await self.store.decide(1, self.one.id, 300, "drop")
        await self.store.decide(1, self.one.id, 100, "post")
        await self.store.posted(1, self.one.id, 100)
        got = await self.store.recent(1, self.one.id)
        self.assertEqual([(a, s) for a, _t, s in got], [(200, "draft"), (100, "posted")])
        self.assertEqual(await self.store.number_of(1, self.one.id, 300), 3)  # a skipped one counts
        self.assertEqual(await self.store.recent(1, self.two.id), [])  # per campaign

    async def test_a_token_is_made_only_for_a_posted_recap_and_new_replaces_it(self) -> None:
        await self.finished(100)
        self.assertIsNone(await self.store.share_token(1, self.one.id, 100))  # a draft has none
        await self.store.decide(1, self.one.id, 100, "post")
        self.assertIsNone(await self.store.share_token(1, self.one.id, 100))  # posting: not yet
        await self.store.posted(1, self.one.id, 100)
        first = await self.store.share_token(1, self.one.id, 100)
        assert first is not None
        self.assertRegex(first, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(await self.store.share_token(1, self.one.id, 100), first)
        fresh = await self.store.share_token(1, self.one.id, 100, new=True)
        self.assertNotEqual(fresh, first)
