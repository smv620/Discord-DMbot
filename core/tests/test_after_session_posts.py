"""The after-session counts go to the DM Bot channel (#3874, group E1, part a): quiet story
threads, private scenes and continuity heads-ups. Each is a count and a button only the DM can use,
so the campaign's people, who read that channel, never see a name. With no DM Bot channel the note
goes to the DM screen as before. Stand-in bots, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.ui import continuity as continuity_ui
from dmbot.ui import private_scenes_list
from dmbot.ui import threads as threads_ui
from tests.fake_import_state import FakeImportState

GUILD, SCREEN, UPKEEP = 1, 50, 60


def bot(*, dm_bot_channel: int | None) -> Any:
    campaign = SimpleNamespace(dm_bot_channel_id=dm_bot_channel, dm_screen_channel_id=SCREEN)
    fake: Any = SimpleNamespace(
        post=AsyncMock(return_value=True),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
    )
    fake._upkeep_channel = lambda c: DMBot._upkeep_channel(fake, c)
    return fake


def table() -> Any:
    return SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, screen_channel_id=SCREEN)


class TheNote(unittest.IsolatedAsyncioTestCase):
    async def test_it_goes_to_the_dm_bot_channel_when_the_campaign_has_one(self) -> None:
        fake = bot(dm_bot_channel=UPKEEP)
        view: Any = object()
        self.assertTrue(await DMBot._post_after_session(fake, table(), "3 things", view))
        fake.post.assert_awaited_once_with(UPKEEP, "3 things", view)

    async def test_it_goes_to_the_dm_screen_when_there_is_no_dm_bot_channel(self) -> None:
        fake = bot(dm_bot_channel=None)
        await DMBot._post_after_session(fake, table(), "3 things")
        fake.post.assert_awaited_once_with(SCREEN, "3 things", None)

    async def test_a_campaign_with_no_saved_channel_uses_the_tables_screen(self) -> None:
        fake = bot(dm_bot_channel=None)
        fake.campaigns.get = AsyncMock(
            return_value=SimpleNamespace(dm_bot_channel_id=None, dm_screen_channel_id=None)
        )
        await DMBot._post_after_session(fake, table(), "x")
        fake.post.assert_awaited_once_with(SCREEN, "x", None)

    async def test_with_no_campaign_it_falls_back_to_the_tables_screen(self) -> None:
        fake = bot(dm_bot_channel=UPKEEP)
        fake.campaigns.get = AsyncMock(return_value=None)
        await DMBot._post_after_session(fake, table(), "x")
        fake.post.assert_awaited_once_with(SCREEN, "x", None)

    async def test_with_no_channel_at_all_nothing_is_posted_and_it_says_so(self) -> None:
        fake = bot(dm_bot_channel=None)
        fake.campaigns.get = AsyncMock(return_value=None)
        gone: Any = SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, screen_channel_id=None)
        self.assertFalse(await DMBot._post_after_session(fake, gone, "x"))
        fake.post.assert_not_awaited()


class TheWords(unittest.TestCase):
    """What goes into a channel the campaign's people read: counts, never what or who."""

    def test_quiet_threads_say_how_many_and_how_long(self) -> None:
        text = threads_ui.waiting_text(3, 4, 1)
        self.assertIn("3", text)
        self.assertNotIn("<@", text)

    def test_continuity_says_how_many_and_never_what(self) -> None:
        text = continuity_ui.waiting_text(2, 1)
        self.assertIn("2 heads-ups", text)
        self.assertIn("1 goes against a pin", text)

    def test_private_scenes_keep_only_the_times(self) -> None:
        self.assertIn("never the words", private_scenes_list.notice(2))


class TheDidTheyMeanCount(unittest.IsolatedAsyncioTestCase):
    """(#3874 E1) the "Did they mean…?" count goes with the others"""

    async def test_the_count_goes_through_the_after_session_post_with_its_button(self) -> None:
        store = SimpleNamespace(
            save=AsyncMock(), count=AsyncMock(return_value=2), forget_speaker=AsyncMock()
        )
        fake: Any = SimpleNamespace(
            heard_questions=store,
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
            _post_after_session=AsyncMock(return_value=True),
        )
        asked = SimpleNamespace(speaker=7, started_ms=1, heard="Zed", options=[("e1", "Zed")])
        tbl = SimpleNamespace(
            guild_id=GUILD,
            campaign_id="c" * 32,
            transcript_session_id="s1",
            unanswered=[asked],
            screen_channel_id=SCREEN,
        )
        await DMBot.save_unanswered(fake, tbl)  # type: ignore[arg-type]
        fake._post_after_session.assert_awaited_once()
        args = fake._post_after_session.await_args.args
        self.assertIs(args[0], tbl)
        self.assertIn("2", args[1])
        self.assertNotIn("Zed", args[1])  # (a count: never the word that was heard)
        self.assertIsNotNone(args[2])  # (the DM-only review button)


class TheNamesShownReport(unittest.IsolatedAsyncioTestCase):
    """(#3874 E1) names said at the table: the names and Undo go to the DMs privately; the DM Bot
    channel gets a count only"""

    async def _run(self, sent: bool) -> tuple[Any, Any]:
        from unittest.mock import patch

        found = [
            SimpleNamespace(entity_id="e1", name="Zed"),
            SimpleNamespace(entity_id="e2", name="Ona"),
        ]
        memory = SimpleNamespace(
            reveal_said=AsyncMock(return_value=SimpleNamespace(value=["e1", "e2"], batch=5)),
            waiting_connections=AsyncMock(return_value=0),
        )
        campaign = SimpleNamespace(dm_bot_channel_id=UPKEEP, dm_screen_channel_id=SCREEN)
        fake: Any = SimpleNamespace(
            memory=memory,
            lookup=None,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        )
        send = AsyncMock(return_value=sent)
        with (
            patch("dmbot.bot.unrevealed_names", AsyncMock(return_value=[])),
            patch("dmbot.bot.said", lambda held, texts: found),
            patch("dmbot.bot.pointer_note.send", send),
        ):
            await DMBot._reveal_said(
                fake, GUILD, "c" * 32, AsyncMock(return_value=["x"]), "Session 1"
            )
        return send, campaign

    async def test_the_names_go_through_the_private_first_note_with_an_undo(self) -> None:
        send, campaign = await self._run(True)
        send.assert_awaited_once()
        args = send.await_args.args
        self.assertIs(args[1], campaign)
        self.assertIn("Zed", args[2])  # the words, which go privately
        self.assertIsNotNone(args[3]())  # the Undo view
        self.assertTrue(send.await_args.kwargs["note"])

    async def test_the_pointer_in_the_dm_bot_channel_is_a_count_with_no_name(self) -> None:
        send, _ = await self._run(True)
        pointer = send.await_args.args[4]
        self.assertIn("2", pointer)
        self.assertNotIn("Zed", pointer)
        self.assertNotIn("Ona", pointer)

    async def test_no_dm_could_be_told_is_only_logged(self) -> None:
        with self.assertLogs("dmbot", level="WARNING") as logs:
            await self._run(False)
        self.assertNotIn("Zed", "".join(logs.output))


class TheImportNamesReport(unittest.IsolatedAsyncioTestCase):
    """(#3874 E1) an import's name report: private first, a count in the DM Bot channel"""

    async def test_the_report_goes_private_first_and_the_pointer_has_no_name(self) -> None:
        from functools import partial
        from unittest.mock import patch

        from dmbot.transcript.name_pass import Candidate, Report, report_to_json

        cand = Candidate("Vane", (), 1, (), ())
        report = Report(unsure=(cand,), new=(cand, cand))
        state = FakeImportState()
        await state.add_strings(
            GUILD,
            "c" * 32,
            "reports",
            ["damaged-row-with-no-colon", "k:{not json", f"k:{report_to_json(report)}"],
        )  # (two damaged rows: each costs only itself, the good one is still reported)
        campaign = SimpleNamespace(
            id="c" * 32, guild_id=GUILD, dm_bot_channel_id=UPKEEP, dm_screen_channel_id=SCREEN
        )
        store = SimpleNamespace(
            pending_scans=AsyncMock(return_value=[]), recap_waiting=AsyncMock(return_value=False)
        )
        fake: Any = SimpleNamespace(
            import_state=state,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            post=AsyncMock(),
            ask_privately=AsyncMock(return_value=True),
        )
        fake._import_open = partial(DMBot._import_open, fake)
        send = AsyncMock(return_value=True)
        with (
            patch("dmbot.bot.pointer_note.send", send),
            patch("dmbot.bot.name_pass_logic.import_text", lambda r: "Zed was fixed"),
            patch("dmbot.bot.name_pass_undo.undo_views_import", lambda cid, fixed: []),
        ):
            await partial(DMBot._tell_import_news, fake)(GUILD, store)  # type: ignore[arg-type]
        send.assert_awaited_once()
        args = send.await_args_list[-1].args
        self.assertIn("Zed", args[2])
        self.assertIn("1 not sure", args[4])
        self.assertIn("2 new", args[4])
        self.assertNotIn("Zed", args[4])


class TheUnhonouredRecapsLine(unittest.IsolatedAsyncioTestCase):
    """(#3874 E1) the line for import recaps that weren't written holds fixed reasons only, so it
    goes to the DM Bot channel when the campaign has one"""

    async def go(self, bot_channel: int | None) -> Any:
        from functools import partial

        campaign = SimpleNamespace(
            id="c" * 32, guild_id=GUILD, dm_bot_channel_id=bot_channel, dm_screen_channel_id=SCREEN
        )
        state = FakeImportState()
        await state.add_each(GUILD, "c" * 32, "unhonoured", ["recaps are switched off"])
        fake: Any = SimpleNamespace(
            import_state=state,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            post=AsyncMock(),
        )
        fake._import_open = partial(DMBot._import_open, fake)
        store = SimpleNamespace(
            pending_scans=AsyncMock(return_value=[]), recap_waiting=AsyncMock(return_value=False)
        )
        await partial(DMBot._tell_import_news, fake)(GUILD, store)  # type: ignore[arg-type]
        return fake

    async def test_it_goes_to_the_dm_bot_channel(self) -> None:
        fake = await self.go(UPKEEP)
        self.assertEqual(fake.post.await_args.args[0], UPKEEP)
        self.assertIn("wasn't written", fake.post.await_args.args[1])

    async def test_with_no_dm_bot_channel_it_stays_on_the_dm_screen(self) -> None:
        fake = await self.go(None)
        self.assertEqual(fake.post.await_args.args[0], SCREEN)


class TheNameAsks(unittest.IsolatedAsyncioTestCase):
    """The words PlotBot was not sure of are kept for the name scan that follows (#4904 3b): per
    session, replaced by a newer set, taken once, other sessions' left alone."""

    def bot(self, *, with_state: bool) -> Any:
        bot: Any = SimpleNamespace(
            import_state=FakeImportState() if with_state else None, _name_pass_asks={}
        )
        bot._take_name_asks = lambda *a: DMBot._take_name_asks(bot, *a)
        return bot

    async def test_in_the_database_per_session_replaced_and_taken_once(self) -> None:
        bot = self.bot(with_state=True)
        keep = DMBot._keep_name_asks
        take = DMBot._take_name_asks
        await keep(bot, GUILD, "c" * 32, "s1", [("Keirandal", 3), ("Vane Oskar", 1)])
        await keep(bot, GUILD, "c" * 32, "s2", [("Fenwick", 2)])
        await keep(bot, GUILD, "c" * 32, "s1", [("Keirandal", 4)])  # (replaces the first set)
        self.assertEqual(await take(bot, GUILD, "c" * 32, "s1"), [("Keirandal", 4)])
        self.assertEqual(await take(bot, GUILD, "c" * 32, "s1"), [])  # (once)
        self.assertEqual(await take(bot, GUILD, "c" * 32, "s2"), [("Fenwick", 2)])  # (kept)

    async def test_without_the_state_it_is_the_old_dict(self) -> None:
        bot = self.bot(with_state=False)
        await DMBot._keep_name_asks(bot, GUILD, "c" * 32, "s1", [("Keirandal", 3)])
        self.assertEqual(bot._name_pass_asks, {"s1": [("Keirandal", 3)]})
        self.assertEqual(
            await DMBot._take_name_asks(bot, GUILD, "c" * 32, "s1"), [("Keirandal", 3)]
        )
        self.assertEqual(bot._name_pass_asks, {})
