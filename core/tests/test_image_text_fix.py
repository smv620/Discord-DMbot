"""The words in a picture of text, said in the table chat with ✏️ Fix (#2450, part 4): the line
helpers, the reply, who may fix, the checks, and that a fix touches the picture's line and never
the same person's speech. The AI and Discord are stand-ins; invented names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot import images
from dmbot.campaigns import CampaignStore
from dmbot.discord_limits import MESSAGE_MAX
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import image_fix
from tests.pg import DatabaseTest
from tests.test_timebot import DM, PLAYER

SESSION = "a" * 32
LINE = 'a picture: A handout. Text written on it: "Meet at the old mill."'


class TheLine(unittest.TestCase):
    def test_the_words_are_found_and_replaced_in_a_picture_of_text(self) -> None:
        self.assertEqual(images.words_of_line(LINE), "Meet at the old mill.")
        fixed = images.line_with_words(LINE, "Meet at the new mill.")
        self.assertEqual(fixed, 'a picture: A handout. Text written on it: "Meet at the new mill."')
        assert fixed is not None
        self.assertEqual(images.words_of_line(fixed), "Meet at the new mill.")

    def test_any_other_line_has_no_words_to_fix(self) -> None:
        for other in (images.NOT_DESCRIBED, "a map: the coast", "a picture: a cat", "hello"):
            self.assertIsNone(images.words_of_line(other))
            self.assertIsNone(images.line_with_words(other, "new words"))

    def test_empty_words_and_a_quote_that_would_end_them_early(self) -> None:
        self.assertIsNone(images.line_with_words(LINE, "   "))
        quoted = images.line_with_words(LINE, 'say "hi"')
        assert quoted is not None
        self.assertEqual(images.words_of_line(quoted), "say 'hi'")

    def test_the_words_are_cleaned_like_the_ais_and_kept_in_bounds(self) -> None:
        fixed = images.line_with_words(LINE, "a‮b\x00c " + "x" * (images.TEXT_MAX + 50))
        assert fixed is not None
        words = images.words_of_line(fixed) or ""
        self.assertLessEqual(len(words), images.TEXT_MAX)
        self.assertNotIn("‮", words)
        self.assertNotIn("\x00", words)


class TheReply(unittest.TestCase):
    def test_the_words_are_quoted_under_the_heading(self) -> None:
        text = image_fix.reply_text("Meet at the old mill.\nBring rope.")
        self.assertEqual(text, "Text in this image:\n> Meet at the old mill.\n> Bring rope.")

    def test_nothing_in_it_can_format_link_or_ping(self) -> None:
        text = image_fix.reply_text("**bold** @everyone https://example.com/x")
        self.assertNotIn("@everyone", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("**bold**", text)

    def test_a_long_handout_fits_one_message(self) -> None:
        text = image_fix.reply_text("\n".join(f"line {n} of the handout" for n in range(400)))
        self.assertLessEqual(len(text), MESSAGE_MAX)


def _interaction(user: int) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        guild=SimpleNamespace(id=1),
        response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
        client=None,
    )


def _bot(*, shown: str = LINE, dms: tuple[int, ...] = (DM,), yes: bool = True) -> Any:
    session = SimpleNamespace(campaign_id="c" * 32)
    bot = SimpleNamespace(
        transcripts=SimpleNamespace(
            session=AsyncMock(return_value=session),
            image_line=AsyncMock(return_value=(shown, shown)),
        ),
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(id="c" * 32, dm_user_ids=frozenset(dms)))
        ),
        consent=SimpleNamespace(has_image_consent=lambda g, u: yes),
        lookup=SimpleNamespace(get=AsyncMock(return_value=object())),
        apply_line_fix=AsyncMock(return_value=True),
        tables={},
    )
    return bot


async def fix(bot: Any, user: int, words: str, *, shown: str = LINE) -> str:
    it = _interaction(user)
    it.client = bot
    with patch("dmbot.ui.image_fix.text_names_a_secret", return_value=False):
        await image_fix.submit(it, SESSION, PLAYER, 1000, shown, words)
    return str(it.followup.send.await_args.args[0])


class WhoMayFix(unittest.IsolatedAsyncioTestCase):
    async def test_the_poster_fixes_their_own_picture_at_once(self) -> None:
        bot = _bot()
        self.assertTrue((await fix(bot, PLAYER, "Meet at the new mill.")).startswith("✅"))
        args = bot.apply_line_fix.await_args
        self.assertEqual(args.args[1:5], ("c" * 32, SESSION, PLAYER, 1000)[0:4])
        self.assertTrue(args.kwargs["image"])
        self.assertEqual(
            images.words_of_line(args.args[5]), "Meet at the new mill."
        )  # the whole line, words replaced

    async def test_a_dm_may_fix_it_too(self) -> None:
        bot = _bot(dms=(DM,))
        self.assertTrue((await fix(bot, DM, "Meet at the new mill.")).startswith("✅"))

    async def test_anyone_else_may_not(self) -> None:
        bot = _bot(dms=(DM,))
        self.assertEqual(await fix(bot, 999, "Meet at the new mill."), image_fix.NOT_YOURS)
        bot.apply_line_fix.assert_not_awaited()

    async def test_nothing_is_written_once_the_poster_stopped(self) -> None:
        bot = _bot(yes=False)
        self.assertEqual(await fix(bot, PLAYER, "Meet at the new mill."), image_fix.STOPPED)
        bot.apply_line_fix.assert_not_awaited()


class TheChecks(unittest.IsolatedAsyncioTestCase):
    async def test_the_same_words_change_nothing(self) -> None:
        bot = _bot()
        self.assertEqual(await fix(bot, PLAYER, "Meet at the old mill."), image_fix.SAME)
        bot.apply_line_fix.assert_not_awaited()

    async def test_a_line_that_changed_since_the_form_opened_is_left_alone(self) -> None:
        bot = _bot(shown=LINE.replace("old", "older"))
        self.assertEqual(await fix(bot, PLAYER, "Meet at the new mill."), image_fix.CHANGED)
        bot.apply_line_fix.assert_not_awaited()

    async def test_a_line_that_is_not_saved_yet_says_to_try_again(self) -> None:
        bot = _bot()
        bot.transcripts.image_line = AsyncMock(return_value=None)
        self.assertEqual(await fix(bot, PLAYER, "Meet at the new mill."), image_fix.NOT_THERE)

    async def test_a_line_with_no_words_in_it_has_nothing_to_fix(self) -> None:
        bot = _bot(shown="a picture: a cat")
        self.assertEqual(
            await fix(bot, PLAYER, "Meet at the new mill.", shown="a picture: a cat"),
            image_fix.NOT_TEXT,
        )

    async def test_words_that_name_a_secret_are_never_written(self) -> None:
        bot = _bot()
        it = _interaction(PLAYER)
        it.client = bot
        with patch("dmbot.ui.image_fix.text_names_a_secret", return_value=True):
            await image_fix.submit(it, SESSION, PLAYER, 1000, LINE, "Meet Zorn at the mill.")
        self.assertEqual(it.followup.send.await_args.args[0], image_fix.SECRET)
        bot.apply_line_fix.assert_not_awaited()

    async def test_a_failed_write_says_so_and_does_not_claim_success(self) -> None:
        bot = _bot()
        bot.apply_line_fix = AsyncMock(return_value=False)
        self.assertEqual(await fix(bot, PLAYER, "Meet at the new mill."), image_fix.NOT_WRITTEN)

    async def test_undo_is_pointed_to_only_while_the_session_runs(self) -> None:
        bot = _bot()
        bot.tables = {1: SimpleNamespace(transcript_session_id=SESSION)}
        self.assertIn("Undo", await fix(bot, PLAYER, "Meet at the new mill."))
        bot.tables = {}
        self.assertNotIn("Undo", await fix(bot, PLAYER, "Meet at the new mill."))


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = TranscriptStore(self.db)
        campaign = await CampaignStore(self.db).create(1, "Frostmaiden", DM)
        self.sid = await self.store.open_session(1, campaign.id, 1_700_000_000)
        at = 1_700_000_000_000
        await self.store.add_lines(
            1,
            self.sid,
            [
                Line(at, PLAYER, "I say hello", "I say hello"),  # speech, the same millisecond
                Line(at, PLAYER, LINE, LINE, typed=True, image=True),
            ],
        )
        self.at = at

    async def test_a_picture_fix_changes_the_picture_line_and_never_the_speech(self) -> None:
        new = LINE.replace("old", "new")
        self.assertEqual(
            await self.store.relabel_line(1, self.sid, PLAYER, self.at, new, image=True), 1
        )
        rows = {x.heard: x.text for x in await self.store.lines(1, self.sid)}
        self.assertEqual(rows[LINE], new)  # what it first read is kept in `heard`
        self.assertEqual(rows["I say hello"], "I say hello")

    async def test_a_speech_fix_never_changes_the_picture_line(self) -> None:
        self.assertEqual(await self.store.relabel_line(1, self.sid, PLAYER, self.at, "I wave"), 1)
        rows = {x.heard: x.text for x in await self.store.lines(1, self.sid)}
        self.assertEqual(rows[LINE], LINE)
        self.assertEqual(rows["I say hello"], "I wave")

    async def test_the_picture_line_is_read_by_its_owner_and_moment_only(self) -> None:
        self.assertEqual(await self.store.image_line(1, self.sid, PLAYER, self.at), (LINE, LINE))
        self.assertIsNone(await self.store.image_line(1, self.sid, DM, self.at))  # not theirs
        self.assertIsNone(await self.store.image_line(1, self.sid, PLAYER, self.at + 1))
        self.assertIsNone(await self.store.image_line(2, self.sid, PLAYER, self.at))  # other server


_ = cast  # (typing helpers kept for the stand-ins above)
