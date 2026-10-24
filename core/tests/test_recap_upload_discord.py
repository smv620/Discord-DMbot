"""Adding a recap someone wrote, from Discord (#2615), through the same database door as the
website: a pasted recap, a file, the one right-to-use question, the question before replacing, and
a non-DM refused. Needs Postgres for the door; the file checks need none."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ui import recap_upload as ru
from dmbot.ui.recap_again import NO_SESSIONS, NOT_YOURS
from tests import recap_fixtures as fx
from tests.test_recap_jobs import Base

TEXT = "Mira and Garrick crossed the Silver Lock at dawn.\n\nThe ferryman said nothing."
OTHER = 4242


def talk(store: Any, campaign: Any, user: int = fx.DM) -> tuple[Any, Any]:
    bot = SimpleNamespace(recaps=store)
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock())
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=user),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return interaction, response.send_message


def said(send: Any) -> str:
    return str(send.await_args.args[0])


class Pure(unittest.TestCase):
    def test_a_file_is_read_as_utf8_with_or_without_a_byte_order_mark(self) -> None:
        self.assertEqual(ru.read_text("héllo".encode()), "héllo")
        self.assertEqual(ru.read_text(b"\xef\xbb\xbfhi"), "hi")
        self.assertIsNone(ru.read_text(b"\xff\xfe\x00\x01"))


def attachment(name: str, data: bytes, size: int | None = None) -> Any:
    return SimpleNamespace(filename=name, size=len(data) if size is None else size,
                           read=AsyncMock(return_value=data))  # fmt: skip


class TheCommand(Base):
    async def invoke(self, file: Any, user: int = fx.DM) -> tuple[Any, Any]:
        bot = SimpleNamespace(
            recaps=self.store,
            campaigns=SimpleNamespace(list_campaigns=AsyncMock(return_value=[self.one])),
            transcripts=SimpleNamespace(sessions=AsyncMock(return_value=[])),
        )
        interaction, send = talk(self.store, self.one, user)
        interaction.client = bot
        await ru.dmbot_recap_upload.callback(interaction, file)
        return interaction, send

    async def test_only_text_files_are_taken(self) -> None:
        _i, send = await self.invoke(attachment("recap.pdf", b"x"))
        self.assertEqual(said(send), ru.WRONG_FILE)

    async def test_a_file_that_is_not_text_or_too_big_is_refused(self) -> None:
        _i, send = await self.invoke(attachment("recap.txt", b"\xff\xfe\x00"))
        self.assertEqual(said(send), ru.NOT_TEXT)
        _i, send = await self.invoke(attachment("recap.md", b"x", size=ru.FILE_MAX_BYTES + 1))
        self.assertEqual(said(send), ru.TOO_BIG)
        _i, send = await self.invoke(attachment("recap.md", b"  \n "))
        self.assertEqual(said(send), ru.OUTCOMES["bad_text"])

    async def test_a_non_dm_is_refused_before_anything_is_asked(self) -> None:
        _i, send = await self.invoke(attachment("recap.txt", TEXT.encode()), user=OTHER)
        self.assertEqual(said(send), NOT_YOURS)

    async def test_a_dm_with_a_good_file_is_asked_which_session(self) -> None:
        _i, send = await self.invoke(attachment("recap.txt", TEXT.encode()))
        self.assertEqual(said(send), NO_SESSIONS)  # (the fake campaign has no sessions)


class Adding(Base):
    def draft(self, at: int = 700, **kw: Any) -> ru.Draft:
        base = {"text": TEXT, "author": "Mira", "title": ""}
        return ru.Draft(self.one, at, **{**base, **kw})

    async def test_the_first_one_asks_about_the_right_to_use_it_once_then_keeps_it(self) -> None:
        interaction, send = talk(self.store, self.one)
        await ru.submit(interaction, self.draft())
        self.assertEqual(said(send), ru.ASK_RIGHTS)
        self.assertIsNone(await self.store.get(1, self.one.id, 700))  # nothing kept yet
        view = send.await_args.kwargs["view"]
        yes = next(c for c in view.children if getattr(c, "label", "") == "Yes, they said OK")
        await yes.callback(interaction)  # the same recap, with the answer
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual((job.summary, job.written_by), (TEXT, "Mira"))
        self.assertEqual(job.title, "Recap by Mira")  # no title given: the text has no short line
        self.assertIn("written by Mira", said(send))

    async def test_a_second_recap_in_the_campaign_is_not_asked_about_the_right_again(self) -> None:
        interaction, send = talk(self.store, self.one)
        await ru.submit(interaction, self.draft(rights=True))
        await ru.submit(interaction, self.draft(at=800))
        self.assertIn("written by Mira", said(send))

    async def test_replacing_asks_first_and_keeps_the_old_one_as_an_earlier(self) -> None:
        interaction, send = talk(self.store, self.one)
        await ru.submit(interaction, self.draft(rights=True))
        await ru.submit(interaction, self.draft(text="A new telling.", author="Garrick"))
        self.assertEqual(said(send), ru.ASK_REPLACE)
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(job.summary, TEXT)  # still the old one
        view = send.await_args.kwargs["view"]
        yes = next(c for c in view.children if getattr(c, "label", "") == "Replace it")
        await yes.callback(interaction)
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual((job.summary, job.written_by), ("A new telling.", "Garrick"))
        self.assertTrue(job.earlier)

    async def test_somebody_who_is_not_a_dm_adds_nothing(self) -> None:
        interaction, send = talk(self.store, self.one, user=OTHER)
        await ru.submit(interaction, self.draft(rights=True))
        self.assertEqual(said(send), NOT_YOURS)
        self.assertIsNone(await self.store.get(1, self.one.id, 700))

    async def test_a_forwarded_question_cannot_be_answered_by_a_stranger(self) -> None:
        interaction, send = talk(self.store, self.one)
        await ru.submit(interaction, self.draft())
        view = send.await_args.kwargs["view"]
        stranger, stranger_send = talk(self.store, self.one, user=OTHER)
        yes = next(c for c in view.children if getattr(c, "label", "") == "Yes, they said OK")
        await yes.callback(stranger)
        self.assertEqual(said(stranger_send), NOT_YOURS)
        self.assertIsNone(await self.store.get(1, self.one.id, 700))

    async def test_empty_or_too_long_words_and_a_missing_name_are_refused(self) -> None:
        interaction, send = talk(self.store, self.one)
        for bad in (self.draft(text="   "), self.draft(text="x" * 50_001), self.draft(author="")):
            await ru.submit(interaction, bad)
            self.assertEqual(said(send), ru.OUTCOMES["bad_text"])
        self.assertIsNone(await self.store.get(1, self.one.id, 700))

    async def test_a_recap_longer_than_a_form_holds_goes_in_whole_by_file(self) -> None:
        interaction, _send = talk(self.store, self.one)
        long_text = ("The party walked on. " * 400).strip()  # 8,400 characters
        self.assertGreater(len(long_text), ru.FORM_MAX)
        await ru.submit(interaction, self.draft(text=long_text, rights=True))
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(job.summary, long_text)
