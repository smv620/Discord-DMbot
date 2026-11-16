"""Where an entry came from, behind every "Where was it said?" button (#4554). No database: the
store is a stand-in, so what is checked is which sources are shown, in which order, and what
the reply says when nothing is kept."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory.models import DM
from dmbot.memory.thread_suggestions import KeptFrom
from dmbot.ui import name_origin, name_places

DAY = 1763164800  # 15 Nov 2025, 00:00 UTC
NOTHING = SimpleNamespace(guild_id=1, id="c" * 32)


class Threads:
    kept: KeptFrom | None = None

    def __init__(self, _db: Any) -> None:
        pass

    async def kept_from(self, *_a: Any) -> KeptFrom | None:
        return Threads.kept


def memory(*, uploads: list[Any] | None = None, source: str = "extractor") -> Any:
    return SimpleNamespace(
        mentions=AsyncMock(return_value=[]),
        name_uploads=AsyncMock(return_value=uploads or []),
        entity=AsyncMock(return_value=SimpleNamespace(source=source)),
        _db=object(),
    )


async def ask(mem: Any, *, transcripts: Any = None, lore: str = "", **kw: Any) -> str:
    client = SimpleNamespace(
        transcripts=transcripts, consent=SimpleNamespace(consenting=AsyncMock(return_value={7}))
    )
    asked = SimpleNamespace(client=client, guild=None)
    with patch.object(name_origin, "ThreadSuggestionStore", Threads):
        return await name_origin.where_entry(
            asked,
            NOTHING,
            mem,
            "e" * 32,
            ["Keegan"],
            lore=lore,
            lore_source="Notes",
            **kw,  # type: ignore[arg-type]
        )


class TheUploadLines(unittest.TestCase):
    def test_it_names_the_upload_and_the_day(self) -> None:
        self.assertEqual(
            name_origin.upload_line("Keegan's lore", DAY),
            "📄 From your upload *Keegan's lore*, 15 Nov. "
            "DMbot doesn't keep the sentence after reading.",
        )

    def test_newest_first_and_no_more_than_three(self) -> None:
        got = name_origin.upload_lines([(str(i), f"U{i}", DAY + i * 86400) for i in range(5)])
        self.assertEqual(len(got), 3)
        self.assertIn("*U4*", got[0])


class TheReply(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        Threads.kept = None

    async def test_a_lore_entry_with_its_sentence_held_shows_the_sentence(self) -> None:
        text = await ask(memory(uploads=[("u", "Notes", DAY)]), lore="Keegan sells maps.")
        self.assertIn("Keegan sells maps.", text)
        self.assertNotIn("DMbot doesn't keep the sentence", text)

    async def test_a_lore_entry_whose_sentence_is_gone_names_the_upload(self) -> None:
        text = await ask(memory(uploads=[("u", "Keegan's lore", DAY)]))
        self.assertIn("📄 From your upload *Keegan's lore*, 15 Nov.", text)

    async def test_a_kept_quest_from_lore_names_its_upload(self) -> None:
        Threads.kept = KeptFrom(None, (), (), "Winter plot", DAY)
        text = await ask(memory())
        self.assertIn("*Winter plot*", text)

    async def test_a_kept_quest_from_a_session_shows_its_lines(self) -> None:
        Threads.kept = KeptFrom("s1", (100, 200), (7,), "", DAY)
        lines = [
            SimpleNamespace(started_ms=100, user_id=7, text="We find the key."),
            SimpleNamespace(started_ms=150, user_id=7, text="Not part of it."),
            SimpleNamespace(started_ms=200, user_id=8, text="Someone who stopped."),
        ]
        session = SimpleNamespace(number=4, started_at=DAY, deleted_at=None)
        transcripts = SimpleNamespace(
            session=AsyncMock(return_value=session),
            lines=AsyncMock(return_value=lines),
            around=AsyncMock(return_value=None),
        )
        text = await ask(memory(), transcripts=transcripts)
        self.assertIn("From session 4, 15 Nov", text)
        self.assertIn("We find the key.", text)
        self.assertNotIn("Not part of it.", text)
        self.assertNotIn("Someone who stopped.", text)

    async def test_a_name_the_dm_typed_says_so(self) -> None:
        self.assertEqual(await ask(memory(source=DM)), name_origin.YOURS)

    async def test_an_entry_with_no_source_at_all_says_no_place_is_kept(self) -> None:
        self.assertEqual(await ask(memory()), name_places.NOTHING_KEPT)

    async def test_the_names_card_says_the_sentence_is_gone_when_nothing_is_left(self) -> None:
        self.assertEqual(await ask(memory(), lore_card=True), name_places.LORE_GONE)

    async def test_a_failed_read_says_try_again(self) -> None:
        mem = memory()
        mem.name_uploads = AsyncMock(side_effect=RuntimeError)
        self.assertEqual(await ask(mem), name_places.TRY_AGAIN)

    async def test_a_kept_session_quest_without_transcripts_still_shows_its_upload(self) -> None:
        Threads.kept = KeptFrom("s1", (100,), (7,), "Winter plot", DAY)
        text = await ask(memory(), transcripts=None)
        self.assertIn("*Winter plot*", text)

    async def test_a_deleted_session_says_the_line_was_removed(self) -> None:
        Threads.kept = KeptFrom("s1", (100,), (7,), "", DAY)
        session = SimpleNamespace(number=4, started_at=DAY, deleted_at=DAY)
        transcripts = SimpleNamespace(
            session=AsyncMock(return_value=session), around=AsyncMock(return_value=None)
        )
        self.assertIn(name_places.REMOVED, await ask(memory(), transcripts=transcripts))
