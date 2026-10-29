"""Giving DMbot an adventure (#1698): each format reads into chunks in order, the same file twice
and a full campaign are refused, only a campaign's DM may give or remove one and only with the
Shared story switch on and the right confirmed, Remove and the campaign's deletion leave no
rows, another campaign sees nothing, the backup round trip, the migration on tables that hold
rows, and a 300-page PDF's time. The store tests need Postgres."""

from __future__ import annotations

import io
import json
import re
import time
import unittest
import uuid
import zipfile
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.models import STORY_OFF, STORY_WATCH
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import adventure_card as ui
from dmbot.dm_screen import story_mode
from dmbot.memory import adventure as adv
from dmbot.memory.adventure import AdventureError, AdventureSection, AdventureStore, chunks_of
from dmbot.memory.document_reader import read_document
from dmbot.memory.name_documents import DocumentError
from dmbot.schema import MIGRATIONS
from tests.answer_first import Timeline
from tests.pg import TEST_URL, DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, OTHER_DM, PLAYER = 7, 8, 9
CLOCK = 1_800_000_000


def docx(*paragraphs: str) -> bytes:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def pdf(pages: list[list[str]]) -> bytes:
    """A real, minimal PDF with these lines of text on each page (no outside library)."""
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, lines in enumerate(pages):
        content = "BT /F1 10 Tf 40 780 Td 12 TL " + " ".join(f"({t}) Tj T*" for t in lines) + " ET"
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R"
            " /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        objs.append(page.encode())
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for k, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{k} 0 obj\n".encode() + body + b"\nendobj\n")
    start = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    )
    return out.getvalue()


STORY = (
    "# The Sunken Keep\n\n"
    "Part one. The party reaches the old keep at dusk and finds the gate open.\n\n"
    "Chapter 2: The Cellar\n\n"
    + "\n\n".join(
        f"Room {n}. A damp room with a cracked floor and a rusted lock. " * 12 for n in range(6)
    )
)


class Cutting(unittest.TestCase):
    def test_chunks_follow_headings_and_stay_in_order_without_losing_words(self) -> None:
        got = chunks_of(STORY)
        self.assertGreater(len(got), 2)
        self.assertEqual(got[0].heading, "The Sunken Keep")
        self.assertIn("Chapter 2: The Cellar", {c.heading for c in got})
        before = re.findall(r"\w+", STORY.replace("#", ""))
        after = re.findall(
            r"\w+",
            " ".join(c.text for c in got)
            + " "
            + " ".join(c.heading for c in got if c.heading not in " ".join(x.text for x in got)),
        )
        self.assertEqual(sorted(set(before)), sorted(set(after)))

    def test_no_chunk_is_longer_than_the_database_allows_or_ends_mid_word(self) -> None:
        text = "\n\n".join("lorem ipsum dolor sit amet " * 90 for _ in range(10))
        for c in chunks_of(text):
            self.assertLessEqual(len(c.text), adv.CHUNK_HARD)
            self.assertFalse(c.text.endswith(("lore", "ipsu", "dolo")), c.text[-12:])
            self.assertTrue(c.text.split()[-1] in {"lorem", "ipsum", "dolor", "sit", "amet"})

    def test_a_word_longer_than_a_chunk_is_cut_rather_than_refused(self) -> None:
        got = chunks_of("x" * 9000)
        self.assertEqual(sum(len(c.text) for c in got), 9000)
        self.assertTrue(all(len(c.text) <= adv.CHUNK_HARD for c in got))

    def test_a_list_of_short_lines_does_not_make_a_chunk_for_each_line(self) -> None:
        text = "\n\n".join(f"Name {n}" for n in range(1000))  # every line looks like a heading
        got = chunks_of(text)
        self.assertLess(len(got), len(text) // adv.MIN_CHUNK + 2)

    def test_empty_text_has_no_chunks(self) -> None:
        self.assertEqual(chunks_of("  \n\n  "), [])


class Reading(unittest.IsolatedAsyncioTestCase):
    async def test_each_format_reads_into_chunks_in_order(self) -> None:
        lines = [
            f"Chapter {n}" if n % 3 == 0 else f"Text of part {n} goes here." for n in range(12)
        ]
        files = {
            "book.txt": "\n\n".join(lines).encode(),
            "book.md": ("# Title\n\n" + "\n\n".join(lines)).encode(),
            "book.docx": docx(*lines),
            "book.pdf": pdf([lines[:6], lines[6:]]),
        }
        for name, raw in files.items():
            text = await read_document(
                name, raw, parse=adv.adventure_text, limit_s=adv.READ_TIME_S,
                max_reply=adv.READ_REPLY_BYTES,
            )  # fmt: skip
            got = chunks_of(text)
            self.assertTrue(got, name)
            joined = " ".join(c.text for c in got)
            positions = [joined.find(f"part {n} ") for n in (1, 2, 4, 5, 7, 8)]
            self.assertEqual(positions, sorted(positions), name)
            self.assertNotIn(-1, positions, name)

    async def test_a_long_word_file_and_a_long_pdf_are_kept_in_full_never_cut(self) -> None:
        lines = [f"Paragraph {n}. " + "word " * 200 for n in range(300)]  # ~310,000 characters
        word = await read_document(
            "long.docx", docx(*lines), parse=adv.adventure_text, limit_s=adv.READ_TIME_S,
            max_reply=adv.READ_REPLY_BYTES,
        )  # fmt: skip
        self.assertIn("Paragraph 299.", word)
        self.assertGreater(len(word), 250_000)
        pages = [[f"Page {p} line {n} " + "text " * 18 for n in range(40)] for p in range(120)]
        book = await read_document(
            "long.pdf", pdf(pages), parse=adv.adventure_text, limit_s=adv.READ_TIME_S,
            max_reply=adv.READ_REPLY_BYTES,
        )  # fmt: skip
        self.assertIn("Page 119 line 39", book)
        self.assertGreater(len(book), 250_000)

    async def test_a_file_past_even_the_adventure_limit_is_refused_not_cut(self) -> None:
        with mock.patch.object(adv, "DOCUMENT_MAX_CHARS", 5_000):
            text = "word " * 2_000  # 10,000 characters
            with self.assertRaises(DocumentError):
                adv.adventure_text("big.txt", text.encode())

    async def test_a_book_longer_than_the_names_screen_takes_is_read(self) -> None:
        big = "\n\n".join(f"Paragraph {n}. " + "word " * 200 for n in range(400))  # ~420,000 chars
        text = await read_document(
            "big.txt", big.encode(), parse=adv.adventure_text, limit_s=adv.READ_TIME_S,
            max_reply=adv.READ_REPLY_BYTES,
        )  # fmt: skip
        self.assertGreater(len(text), 400_000)  # past the names screen's 200,000


class Setup(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(CLOCK)
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.campaigns.register_section(AdventureSection())
        self.store = AdventureStore(self.db, clock=lambda: self.now)
        self.a = await self.campaigns.create(GUILD_A, "Frostmaiden", DM)
        self.b = await self.campaigns.create(GUILD_B, "Elsewhere", OTHER_DM)

    async def switch_on(self, campaign: Any = None) -> None:
        c = campaign or self.a
        await self.campaigns.set_story_mode(
            c.guild_id, c.id, STORY_WATCH, confirmed_by=next(iter(c.dm_user_ids))
        )

    async def count(self, table: str, guild: int = GUILD_A) -> int:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])


class Storing(Setup):
    async def test_a_file_is_kept_in_chunks_in_order(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "The Sunken Keep.pdf", STORY)
        chunks = await self.store.chunks(GUILD_A, self.a.id, doc.id)
        self.assertEqual([c.text for c in chunks], [c.text for c in chunks_of(STORY)])
        self.assertEqual((doc.chunks, doc.characters, doc.added_by), (len(chunks), len(STORY), DM))
        (listed,) = await self.store.documents(GUILD_A, self.a.id)
        self.assertEqual(listed, doc)

    async def test_the_same_file_twice_is_refused(self) -> None:
        await self.switch_on()
        await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        with self.assertRaises(AdventureError) as raised:
            await self.store.add(GUILD_A, self.a.id, DM, "renamed.txt", STORY)
        self.assertEqual(str(raised.exception), adv.ALREADY)
        self.assertEqual(await self.count("adventure_documents"), 1)

    async def test_the_campaign_cap_is_enforced_with_plain_words(self) -> None:
        await self.switch_on()
        with mock.patch.object(adv, "CAMPAIGN_MAX_CHARS", 1000):
            await self.store.add(GUILD_A, self.a.id, DM, "a.txt", "alpha " * 100)  # 600
            with self.assertRaises(AdventureError) as raised:
                await self.store.add(GUILD_A, self.a.id, DM, "b.txt", "bravo " * 100)
        self.assertEqual(str(raised.exception), adv.FULL)

    async def test_the_number_of_documents_is_capped(self) -> None:
        await self.switch_on()
        with mock.patch.object(adv, "DOCUMENTS_MAX", 2):
            await self.store.add(GUILD_A, self.a.id, DM, "1.txt", "one " * 10)
            await self.store.add(GUILD_A, self.a.id, DM, "2.txt", "two " * 10)
            with self.assertRaises(AdventureError) as raised:
                await self.store.add(GUILD_A, self.a.id, DM, "3.txt", "three " * 10)
        self.assertEqual(str(raised.exception), adv.TOO_MANY)

    async def test_only_a_dm_with_the_switch_on_and_the_right_confirmed_may_give_one(self) -> None:
        with self.assertRaises(AdventureError) as off:  # switch Off, nothing confirmed
            await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        self.assertEqual(str(off.exception), adv.NOT_ON)
        await self.switch_on()
        with self.assertRaises(AdventureError) as notdm:
            await self.store.add(GUILD_A, self.a.id, PLAYER, "a.txt", STORY)
        self.assertEqual(str(notdm.exception), adv.NOT_DM)
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_OFF)  # off again
        with self.assertRaises(AdventureError):
            await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        self.assertEqual(await self.count("adventure_documents"), 0)

    async def test_remove_deletes_the_documents_chunks_and_only_for_a_dm(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        self.assertGreater(await self.count("adventure_chunks"), 0)
        with self.assertRaises(AdventureError):
            await self.store.remove(GUILD_A, self.a.id, PLAYER, doc.id)
        await self.store.remove(GUILD_A, self.a.id, DM, doc.id)
        self.assertEqual(
            (await self.count("adventure_documents"), await self.count("adventure_chunks")), (0, 0)
        )
        with self.assertRaises(AdventureError) as gone:
            await self.store.remove(GUILD_A, self.a.id, DM, doc.id)
        self.assertEqual(str(gone.exception), adv.GONE_DOC)

    async def test_deleting_the_campaign_leaves_no_rows(self) -> None:
        await self.switch_on()
        await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM campaigns WHERE id = %s", (self.a.id,))
        self.assertEqual(
            (await self.count("adventure_documents"), await self.count("adventure_chunks")), (0, 0)
        )

    async def test_another_campaign_and_another_server_see_nothing(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        self.assertEqual(await self.store.documents(GUILD_B, self.b.id), [])
        self.assertEqual(await self.store.chunks(GUILD_B, self.b.id, doc.id), [])
        self.assertEqual(await self.count("adventure_chunks", GUILD_B), 0)  # row-level security
        with self.assertRaises(AdventureError):  # not through the wrong campaign either
            await self.store.remove(GUILD_B, self.b.id, OTHER_DM, doc.id)
        await self.switch_on(self.b)
        await self.store.add(GUILD_B, self.b.id, OTHER_DM, "a.txt", STORY)  # same text, own copy
        self.assertEqual(len(await self.store.documents(GUILD_A, self.a.id)), 1)


class BackingUp(Setup):
    async def test_the_backup_holds_the_list_and_never_the_text(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        (entry,) = data["sections"]["adventure"]
        self.assertEqual(
            set(entry), {"file_name", "fingerprint", "characters", "added_by", "added_at"}
        )
        self.assertNotIn(STORY[:40], json.dumps(data))  # no chunk text anywhere in the backup
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        (back,) = await self.store.documents(GUILD_B, restored.id)
        self.assertEqual(
            (back.file_name, back.characters, back.chunks, back.has_text),
            (doc.file_name, doc.characters, 0, False),
        )
        self.assertEqual(await self.store.chunks(GUILD_B, restored.id, back.id), [])
        self.assertIn("give DMbot this file again", ui.card_text([back]))
        # A restored copy never stands in for the DM's own press (#1692): no button, no more files.
        self.assertFalse(await self.campaigns.story_confirmed(GUILD_B, restored.id))
        with self.assertRaises(AdventureError):
            await self.store.add(GUILD_B, restored.id, DM, "a.txt", STORY)

    async def test_giving_the_same_file_again_fills_in_a_restored_document(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        await self.campaigns.set_story_mode(
            GUILD_B, restored.id, STORY_WATCH, confirmed_by=DM
        )  # the DM says here they have the right
        other = await self.store.add(GUILD_B, restored.id, DM, "b.txt", STORY + " more")
        self.assertTrue(other.has_text)  # a different file is just a new document
        filled = await self.store.add(GUILD_B, restored.id, DM, "renamed.txt", STORY)
        docs = await self.store.documents(GUILD_B, restored.id)
        self.assertEqual(len(docs), 2)
        mine = next(d for d in docs if d.id == filled.id)
        self.assertTrue(mine.has_text)
        self.assertEqual(mine.chunks, doc.chunks)
        self.assertEqual(
            [c.text for c in await self.store.chunks(GUILD_B, restored.id, mine.id)],
            [c.text for c in chunks_of(STORY)],
        )
        with self.assertRaises(AdventureError):  # now it is a repeat
            await self.store.add(GUILD_B, restored.id, DM, "a.txt", STORY)

    async def test_a_damaged_adventure_section_is_refused_whole(self) -> None:
        await self.switch_on()
        await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        good = await self.campaigns.export(GUILD_A, self.a.id)
        damages: tuple[Callable[[Any], object], ...] = (
            lambda d: d.update(fingerprint="nope"),
            lambda d: d.update(added_by="0"),
            lambda d: d.update(characters=0),
            lambda d: d.update(extra=1),
            lambda d: d.update(chunks=[{"heading": "", "text": "text smuggled in"}]),  # no text
            lambda d: d.update(file_name="bad\nname.txt"),
            lambda d: d.update(file_name="x\ud800.txt"),
            lambda d: d.update(characters=2**40),  # more than a campaign may hold
        )
        for damage in damages:
            data = await self.campaigns.export(GUILD_A, self.a.id)
            damage(data["sections"]["adventure"][0])
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertEqual(await self.count("adventure_documents", GUILD_B), 0)
        again = await self.campaigns.import_backup(GUILD_B, good, DM)  # the good one still loads
        self.assertEqual(len(await self.store.documents(GUILD_B, again.id)), 1)

    async def test_replacing_a_campaign_replaces_its_adventure_too(self) -> None:
        await self.switch_on()
        await self.store.add(GUILD_A, self.a.id, DM, "old.txt", "old text " * 30)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        await self.store.remove(
            GUILD_A, self.a.id, DM, (await self.store.documents(GUILD_A, self.a.id))[0].id
        )
        await self.store.add(GUILD_A, self.a.id, DM, "new.txt", "new text " * 30)
        await self.campaigns.import_backup(GUILD_A, data, DM, replace_campaign_id=self.a.id)
        self.assertEqual(
            [d.file_name for d in await self.store.documents(GUILD_A, self.a.id)], ["old.txt"]
        )


class Migration(Setup):
    async def test_the_migration_on_a_database_that_already_holds_campaigns(self) -> None:
        if not TEST_URL:
            self.skipTest("needs the test database")
        schema = f"t_{uuid.uuid4().hex[:16]}"
        before = MIGRATIONS[: [n for n, _ in MIGRATIONS].index("0122_adventure_text")]
        db = await Database.open(TEST_URL, schema=schema, max_size=2, migrations=before)
        try:
            old = await CampaignStore(db).create(GUILD_A, "Old table", DM)
            await db.migrate(MIGRATIONS)
            self.assertEqual(await AdventureStore(db).documents(GUILD_A, old.id), [])
            async with db.guild(
                GUILD_A
            ) as conn:  # forced row-level security: another server sees none
                cur = await conn.execute("SELECT count(*) AS n FROM adventure_documents")
                row = await cur.fetchone()
            assert row is not None and row["n"] == 0
        finally:
            await db.close()
            await drop_schema(TEST_URL, schema)


class Speed(Setup):
    async def test_a_300_page_pdf_is_read_and_kept_in_one_go(self) -> None:
        await self.switch_on()
        row = "The party walks through room {}.{} and finds a long winding corridor of old stones"
        pages = [
            [f"Chapter {p}" if line == 0 else row.format(p, line) for line in range(28)]
            for p in range(300)
        ]
        raw = pdf(pages)
        began = time.monotonic()
        text = await read_document(
            "big.pdf", raw, parse=adv.adventure_text, limit_s=adv.READ_TIME_S,
            max_reply=adv.READ_REPLY_BYTES,
        )  # fmt: skip
        read_s = time.monotonic() - began
        began = time.monotonic()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "big.pdf", text)
        store_s = time.monotonic() - began
        print(
            f"\n300-page PDF: {len(raw)} bytes, {len(text)} characters, {doc.chunks} chunks; "
            f"read {read_s:.1f} s, stored {store_s:.1f} s"
        )
        self.assertGreater(len(text), 500_000)
        self.assertLess(read_s, adv.READ_TIME_S)
        self.assertLess(store_s, 20)
        self.assertEqual(await self.count("adventure_chunks"), doc.chunks)


# ---- the card -------------------------------------------------------------------------------


def sent_to(out: list[Any]) -> Any:
    def record(*args: Any, **kwargs: Any) -> None:
        out.append(args[0])

    return record


def press(user: int, guild: int = GUILD_A) -> Any:
    sent: dict[str, Any] = {}

    async def send_message(content: str = "", **kw: Any) -> None:
        sent.update(content=content, **kw)

    async def edit_message(**kw: Any) -> None:
        sent.update(edited=True, **kw)

    async def edit_original_response(**kw: Any) -> None:
        sent.update(edited=True, **kw)

    member = MagicMock(spec=discord.Member)
    member.id = user
    it = MagicMock()
    it.guild = SimpleNamespace(id=guild)
    it.guild_id = guild
    it.user = member
    it.response.send_message = send_message
    it.response.edit_message = edit_message
    it.response.send_modal = AsyncMock()
    done = {"yes": False}

    async def defer(**kw: Any) -> None:
        done["yes"] = True

    async def follow(content: str = "", **kw: Any) -> None:
        sent.update(content=content, **kw)

    it.response.defer = defer
    it.response.is_done = lambda: done["yes"]
    it.followup.send = follow  # (a refusal after a defer arrives as a follow-up)
    it.edit_original_response = edit_original_response
    it.sent = sent
    return it


class Card(Setup):
    def pressing(self, user: int, guild: int = GUILD_A) -> Any:
        it = press(user, guild)
        it.client = SimpleNamespace(campaigns=self.campaigns, adventure=self.store)
        return it

    def labels(self, it: Any) -> list[str]:
        return [str(getattr(getattr(c, "item", c), "label", "")) for c in it.sent["view"].children]

    async def test_the_card_buttons_answer_discord_before_the_database(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        doc = await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        for button in (
            ui.RemoveButton(self.a.id, doc.id),
            ui.RemoveYesButton(self.a.id, doc.id),
            ui.RemoveNoButton(self.a.id),
            ui.BackToFilesButton(self.a.id),
            ui.RefreshButton(self.a.id),
            ui.ReadNoButton(self.a.id),
        ):
            line = Timeline()
            it = self.pressing(DM)
            it.response, it.edit_original_response = line.response, line.edit_original_response
            it.followup = line.followup
            real = self.campaigns
            it.client = SimpleNamespace(
                campaigns=SimpleNamespace(
                    get=line.slow("campaign", await real.get(GUILD_A, self.a.id)),
                    story_confirmed=line.slow("confirmed", True),
                ),
                adventure=self.store,
            )
            await button.callback(it)
            line.assert_answered_first()
            self.assertIn("slow:campaign", line.events, type(button).__name__)

    async def test_read_see_and_match_answer_discord_before_the_database(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        for button in (
            ui.ReadButton(self.a.id),
            ui.SeeButton(self.a.id, 0),
            ui.MatchButton(self.a.id),
        ):
            line = Timeline()
            it = self.pressing(DM)
            it.response, it.edit_original_response = line.response, line.edit_original_response
            it.followup = line.followup
            it.client = SimpleNamespace(
                campaigns=SimpleNamespace(
                    get=line.slow("campaign", await self.campaigns.get(GUILD_A, self.a.id)),
                    story_confirmed=line.slow("confirmed", True),
                ),
                adventure=self.store,
                memory=None,
            )
            await button.callback(it)
            line.assert_answered_first()
            self.assertIn("slow:campaign", line.events, type(button).__name__)

    async def test_a_refusal_after_the_answer_is_a_private_follow_up(self) -> None:
        sent: list[str] = []
        it = self.pressing(PLAYER)
        await it.response.defer()

        async def follow(text: str, **kw: Any) -> None:
            sent.append(text)

        it.followup.send = follow
        await ui.RefreshButton(self.a.id).callback(it)
        self.assertEqual(sent, [story_mode.ONLY_DMS])
        it = self.pressing(DM)
        await it.response.defer()
        it.followup.send = follow
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_OFF, confirmed_by=None)
        await ui.RefreshButton(self.a.id).callback(it)
        self.assertEqual(sent[-1], ui.NOT_ON)

    async def test_no_button_with_the_switch_off_or_without_a_confirmation(self) -> None:
        it = self.pressing(DM)
        await story_mode.StoryButton(self.a.id).callback(it)
        self.assertNotIn("Give your adventure", self.labels(it))
        await self.switch_on()
        fresh = await self.campaigns.get(GUILD_A, self.a.id)
        assert fresh is not None
        it = self.pressing(DM)
        await story_mode.StoryButton(self.a.id).callback(it)
        self.assertIn("Give your adventure", self.labels(it))
        # A restored campaign has the mode but no press of its own: no button.
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        it = self.pressing(DM, guild=GUILD_B)
        await story_mode.StoryButton(restored.id).callback(it)
        self.assertNotIn("Give your adventure", self.labels(it))

    async def test_a_non_dm_and_a_switched_off_card_are_refused_on_every_button(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "a.txt", STORY)
        for button in (
            ui.AdventureButton(self.a.id),
            ui.AddButton(self.a.id),
            ui.RemoveButton(self.a.id, doc.id),
            ui.RemoveYesButton(self.a.id, doc.id),
            ui.RemoveNoButton(self.a.id),
        ):
            it = self.pressing(PLAYER)
            await button.callback(it)
            self.assertEqual(it.sent["content"], story_mode.ONLY_DMS, type(button).__name__)
        self.assertEqual(len(await self.store.documents(GUILD_A, self.a.id)), 1)
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_OFF)  # an old card, later
        for button in (ui.AdventureButton(self.a.id), ui.AddButton(self.a.id)):
            it = self.pressing(DM)
            await button.callback(it)
            self.assertEqual(it.sent["content"], ui.NOT_ON, type(button).__name__)

    async def test_the_card_lists_what_is_kept_and_remove_asks_once(self) -> None:
        await self.switch_on()
        doc = await self.store.add(GUILD_A, self.a.id, DM, "The Sunken Keep.pdf", STORY)
        it = self.pressing(DM)
        await ui.AdventureButton(self.a.id).callback(it)
        text = it.sent["content"]
        self.assertIn("The Sunken Keep.pdf", text)
        self.assertIn(f"<@{DM}>", text)
        self.assertIn("isn't used for anything yet", text)
        self.assertEqual(self.labels(it), ["Remove 1", "Give a file"])
        ask = self.pressing(DM)
        await ui.RemoveButton(self.a.id, doc.id).callback(ask)
        self.assertIn("Remove **The Sunken Keep.pdf**?", ask.sent["content"])
        self.assertEqual(len(await self.store.documents(GUILD_A, self.a.id)), 1)  # not yet
        no = self.pressing(DM)
        await ui.RemoveNoButton(self.a.id).callback(no)
        self.assertEqual(len(await self.store.documents(GUILD_A, self.a.id)), 1)
        yes = self.pressing(DM)
        await ui.RemoveYesButton(self.a.id, doc.id).callback(yes)
        self.assertEqual(await self.store.documents(GUILD_A, self.a.id), [])
        self.assertIn("Removed. DMbot forgot it.", yes.sent["content"])

    async def test_twenty_documents_with_long_names_still_fit_one_message(self) -> None:
        docs = [
            adv.Document(uuid.uuid4().hex, "N" * 200, 5_000_000 // 20, 100, DM, CLOCK)
            for _ in range(adv.DOCUMENTS_MAX)
        ]
        campaign = await self._campaign()
        self.assertLess(len(f"Kept.\n\n{ui.card_text(docs)}"), 2000)
        self.assertEqual(len(ui.card_view(campaign, docs).children), adv.DOCUMENTS_MAX)
        one = adv.Document(uuid.uuid4().hex, "a.txt", 100, 1, DM, CLOCK)
        self.assertIn("(about 1 page", ui.card_text([one]))

    async def test_the_add_button_opens_the_form_for_a_dm(self) -> None:
        await self.switch_on()
        it = self.pressing(DM)
        await ui.AddButton(self.a.id).callback(it)
        (form,) = it.response.send_modal.await_args.args
        self.assertEqual(form.campaign.id, self.a.id)

    async def test_the_form_reads_a_file_and_keeps_it(self) -> None:
        await self.switch_on()
        form = ui.AdventureForm(await self._campaign())
        upload = form.file.component
        attachment = SimpleNamespace(
            filename="The Sunken Keep.txt",
            size=len(STORY),
            read=AsyncMock(return_value=STORY.encode()),
        )
        sent: list[Any] = []
        it = self.pressing(DM)
        it.response.defer = AsyncMock()
        it.followup.send = AsyncMock(side_effect=lambda *a, **k: sent.append((a, k)))
        with mock.patch.object(type(upload), "values", new=[attachment], create=True):
            await form.on_submit(it)
        (doc,) = await self.store.documents(GUILD_A, self.a.id)
        self.assertEqual(doc.file_name, "The Sunken Keep.txt")
        self.assertIn("Kept.", sent[-1][0][0])

    async def test_the_form_refuses_a_big_or_wrong_file_and_a_non_dm(self) -> None:
        await self.switch_on()
        campaign = await self._campaign()
        for filename, size, user, want in (
            ("big.pdf", adv.FILE_MAX_BYTES + 1, DM, ui.TOO_BIG),
            ("pic.png", 10, DM, ui.WRONG_TYPE),
            ("a.txt", 10, PLAYER, "Only this campaign's DMs can do that."),
        ):
            form = ui.AdventureForm(campaign)
            attachment = SimpleNamespace(
                filename=filename, size=size, read=AsyncMock(return_value=b"x")
            )
            sent: list[Any] = []
            it = self.pressing(user)
            it.response.defer = AsyncMock()
            it.followup.send = AsyncMock(side_effect=sent_to(sent))
            with mock.patch.object(
                type(form.file.component), "values", new=[attachment], create=True
            ):
                await form.on_submit(it)
            self.assertEqual(sent[-1], want, filename)
        self.assertEqual(await self.store.documents(GUILD_A, self.a.id), [])

    async def test_the_buttons_come_back_from_their_ids_after_a_restart(self) -> None:
        doc = uuid.uuid4().hex
        for cls, custom_id in (
            (ui.AdventureButton, f"dmbot:adv:{self.a.id}"),
            (ui.AddButton, f"dmbot:advadd:{self.a.id}"),
            (ui.RemoveButton, f"dmbot:advrm:{self.a.id}:{doc}"),
            (ui.RemoveYesButton, f"dmbot:advrmyes:{self.a.id}:{doc}"),
            (ui.RemoveNoButton, f"dmbot:advrmno:{self.a.id}"),
        ):
            match = cls.__discord_ui_compiled_template__.fullmatch(custom_id)
            assert match is not None, custom_id
            made = await cls.from_custom_id(MagicMock(), MagicMock(), match)
            self.assertEqual(made.campaign_id, self.a.id)

    async def _campaign(self) -> Any:
        found = await self.campaigns.get(GUILD_A, self.a.id)
        assert found is not None
        return found
