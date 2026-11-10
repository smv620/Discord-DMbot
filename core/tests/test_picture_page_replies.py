"""The three Discord replies that follow a PDF upload say, in counts only, how many pages had only
pictures (#4000, #4029): the names/lore document, the house rules file and the Shared story. A
text file, or a PDF with no picture-only page, says nothing about pictures. No database and no
network: stand-ins for the AI and the store, and small PDFs made here."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.dm_screen import adventure_card
from dmbot.dm_screen import house_sync as hs
from dmbot.ui import house_file as file_ui
from dmbot.ui import name_background, name_lists
from tests.test_adventure import pdf
from tests.test_house_any_file import FakeAI, answer, one
from tests.test_house_sync import SyncTest, campaign

DM = 7001
SENTENCE = (
    "We roll initiative once at the start of a fight and keep the same order all session long."
)
ANSWER_SAYS = "**1 of 3 pages** had only pictures"
# two pages of text and one page with only a picture on it: 1 of 3
WITH_PICTURE = [[SENTENCE], [], [SENTENCE]]
ALL_TEXT = [[SENTENCE], [SENTENCE], [SENTENCE]]


class NamesDocument(unittest.IsolatedAsyncioTestCase):
    async def offer_for(self, raw: bytes, name: str) -> str:
        upload, problem = await name_lists._document(name, raw, name)
        assert upload is not None, problem
        return name_background.offer_text(upload, False)

    async def test_names_document_says_it_when_a_page_is_pictures_only(self) -> None:
        text = await self.offer_for(pdf(WITH_PICTURE), "names.pdf")
        self.assertIn(ANSWER_SAYS, text)

    async def test_names_document_says_nothing_for_an_all_text_pdf(self) -> None:
        text = await self.offer_for(pdf(ALL_TEXT), "names.pdf")
        self.assertNotIn("only pictures", text)

    async def test_a_text_file_has_no_note(self) -> None:
        upload, _ = await name_lists._document("names.txt", SENTENCE.encode(), "names.txt")
        assert upload is not None
        self.assertEqual(upload.note, "")


class HouseRules(SyncTest):
    def setUp(self) -> None:
        super().setUp()
        hs._ai_busy.clear()
        hs._ai_reads.clear()
        self.bot.plan_allows = AsyncMock(return_value=True)

    async def reply_for(self, raw: bytes, name: str, ai: FakeAI) -> str:
        self.bot.house_file_ai = ai
        form = file_ui.UploadForm(campaign())
        attachment = SimpleNamespace(size=len(raw), filename=name, read=AsyncMock(return_value=raw))
        form.file.component._values = [attachment]  # type: ignore[attr-defined]
        it = self.interaction()
        await form.on_submit(it)
        return str(it.followup.send.await_args.args[0])

    async def test_house_rules_says_it_when_a_page_is_pictures_only(self) -> None:
        ai = FakeAI(answer([one("Roll once.", "roll initiative once at the start of a fight")]))
        said = await self.reply_for(pdf(WITH_PICTURE), "our-rules.pdf", ai)
        self.assertIn(ANSWER_SAYS, said)
        self.assertNotIn("None", said)  # (the offer was sent: the note stands alone)

    async def test_house_rules_says_nothing_for_an_all_text_pdf(self) -> None:
        ai = FakeAI(answer([one("Roll once.", "roll initiative once at the start of a fight")]))
        said = await self.reply_for(pdf(ALL_TEXT), "our-rules.pdf", ai)
        self.assertIn("I found 1 new house rule", said)
        self.assertNotIn("only pictures", said)


class SharedStory(SyncTest):
    async def kept_reply(self, raw: bytes) -> str:
        """The Shared story's add, with the store and the card stood in for."""
        store = SimpleNamespace(add=AsyncMock())
        dm_campaign: Any = SimpleNamespace(guild_id=5, id="c" * 32, dm_user_ids=[DM])
        client = SimpleNamespace(
            adventure=store,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=dm_campaign)),
        )
        it = self.interaction()
        it.client = client
        it.guild = SimpleNamespace(id=5)
        it.user = SimpleNamespace(id=DM)
        form = adventure_card.AdventureForm(dm_campaign)
        upload = form.file.component
        attachment = SimpleNamespace(
            filename="The Keep.pdf", size=len(raw), read=AsyncMock(return_value=raw)
        )
        with (
            patch.object(type(upload), "values", new=[attachment], create=True),
            patch.object(adventure_card, "_card", AsyncMock(return_value=("CARD", None))),
        ):
            await form.on_submit(it)
        return str(it.followup.send.await_args.args[0])

    async def test_shared_story_says_it_when_a_page_is_pictures_only(self) -> None:
        said = await self.kept_reply(pdf(WITH_PICTURE))
        self.assertTrue(said.startswith("Kept.\n**1 of 3 pages** had only pictures"))
        self.assertTrue(said.endswith("\n\nCARD"))
        self.assertIn(ANSWER_SAYS, said)

    async def test_shared_story_says_nothing_for_an_all_text_pdf(self) -> None:
        said = await self.kept_reply(pdf(ALL_TEXT))
        self.assertEqual(said, "Kept.\n\nCARD")
